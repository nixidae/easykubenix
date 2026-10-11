# Render a chart and return its objects as two lists: `crds` for
# `kubernetes.crds` and `objects` for `kubernetes.objects`. Objects
# come back exactly as Helm emitted them: routing (`ekn.deploymentUnit`) and
# annotation stamping are the caller's business.
{
  runCommand,
  lib,
  kubernetes-helm,
  fetchHelm,
  ekn-yaml2json,
}:
{
  # { repoURL, name, version, sha256 }
  chart,
  # Helm release name.
  name,
  namespace,
  values ? { },
  kubeVersion ? null,
  noHooks ? false,
  # API versions to claim the target cluster has, for a chart gating templates
  # on `.Capabilities.APIVersions.Has`. There is no cluster here, so without
  # this the list is empty and every such check answers "absent" -- usually by
  # emitting nothing at all, which renders and applies and reports healthy. See
  # `helm.charts.<name>.apiVersions` in helm.nix, which carries the detail,
  # including that Helm matches these strings literally.
  apiVersions ? null,
}:
let
  chartDrv = fetchHelm {
    chart = chart.name;
    repo = chart.repoURL;
    version = chart.version;
    sha256 = chart.sha256;
  };
  valuesJsonFile = builtins.toFile "${name}-values.json" (builtins.toJSON values);
  # `helm template` runs as a plain (non-fixed-output) derivation, so IFD only
  # pays for it once per set of inputs (chart/values/name/namespace) -- Nix
  # caches the build in the store like any other derivation. This replaced a
  # `builtins.renderHelm` primop call: that primop round-trips every single
  # eval through nanopynix's Python worker AND a separate gRPC hop to a Go
  # subprocess (which itself execs `helm template` and JSON-marshals the
  # result back across that boundary), none of which Nix caches between
  # evaluations. Benchmarking showed even a tiny chart (~4 objects) cost ~2s
  # per eval through that path with no dependence on content size -- a fixed
  # RPC/marshalling floor, not module-system overhead. `helm template`
  # executed as a derivation is a one-time cost; parsing its cached output
  # then happens entirely in-process (see `parsed` below), no subprocess/RPC
  # hop at all.
  resourcesYaml = runCommand "${name}-rendered.yaml" { nativeBuildInputs = [ kubernetes-helm ]; } ''
    helm template "${name}" \
      --namespace "${namespace}" \
      --include-crds \
      ${lib.optionalString (values != { }) "-f ${valuesJsonFile}"} \
      ${lib.optionalString (kubeVersion != null) "--kube-version ${kubeVersion}"} \
      ${lib.optionalString noHooks "--no-hooks"} \
      ${
        lib.optionalString (
          apiVersions != null && apiVersions != [ ]
        ) "--api-versions ${lib.concatStringsSep "," apiVersions}"
      } \
      ${chartDrv} > $out
  '';
  # See lib/parseYamlStream.nix for why go-yaml is the only reader here.
  # A list, not a namespace/kind/name tree: `kubernetes.objects` files a list
  # definition itself, chooses each key, and refuses two different objects with one identity.
  parsed = builtins.fromJSON (
    builtins.readFile (
      runCommand "${name}-rendered.json" { nativeBuildInputs = [ ekn-yaml2json ]; } ''
        ekn-yaml2json --shape list < ${resourcesYaml} > $out
      ''
    )
  );
  # CustomResourceDefinitions carry large OpenAPI schemas, and
  # `kubernetes.crds` keeps them out of `ekn.lib.kubeValueType`'s per-leaf
  # check. Measured on 371 objects with 78 CRDs: 11.4s, against 12.8s with
  # the CRDs typed.
  split = lib.partition (object: object.kind or null == "CustomResourceDefinition") parsed;
in
{
  crds = split.right;
  objects = split.wrong;
}
