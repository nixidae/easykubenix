# Checks and measurements for ./module.nix against the real easykubenix.
#
#   nix eval --impure --json --expr \
#     'import ./design/kinds/kubenix-submodule { kubenixSrc = <checkout or null>; case = "<case>"; }'
#
# `kubenixSrc` reaches the module through easykubenix's existing `specialArgs`
# parameter, which is what a `kubenixSrc ? null` argument would forward.
{
  kubenixSrc ? null,
  case,
  n ? 0,
}:
let
  sources = import ../../../nix/sources.nix;
  pkgs = import sources.nixpkgs { };
  inherit (pkgs) lib;
  bridge = ./module.nix;

  # N plain easykubenix ConfigMaps, so a run has work of its own to do.
  plain = {
    kubernetes.objects.default.ConfigMap = lib.listToAttrs (
      map (i: lib.nameValuePair "cm${toString i}" { data.i = toString i; }) (lib.range 1 n)
    );
  };

  ekn =
    modules:
    import ../../../default.nix {
      inherit pkgs modules;
      specialArgs = { inherit kubenixSrc; };
    };
  generated =
    modules: builtins.stringLength (builtins.toJSON (ekn modules).config.kubernetes.generated);
  docs = modules: lib.length (lib.optionAttrSetToDocList (ekn modules).options);

  instance = {
    kubenix = {
      imports = [
        "${kubenixSrc}/docs/content/examples/deployment/module.nix"
        "${kubenixSrc}/docs/content/examples/custom-resources/module.nix"
        (
          { config, eknConfig, ... }:
          {
            kubernetes.namespace = "web";
            # Reads of the kubenix configuration and of the easykubenix one.
            kubernetes.resources.deployments.nginx.metadata.annotations = {
              # `kubernetes.resources` is kubenix's untyped alias, with no
              # `metadata.name` default; the typed value is under `api`.
              config-map = config.kubernetes.api.resources.core.v1.ConfigMap.nginx-config.metadata.name;
              ip-families = lib.concatStringsSep "," eknConfig.kubernetes.clusterInfo.ipFamilies;
            };
            # The list form, beside the example's attribute-set form.
            kubernetes.resources.deployments.nginx.spec.template.spec.containers = [
              {
                name = "sidecar";
                image = "busybox";
              }
            ];
          }
        )
      ];
    };
  };
  patches = {
    # Typed, through kubenix's own option.
    kubenix.kubernetes.resources.deployments.nginx.spec.template.spec.containers.nginx.image =
      lib.mkForce "nginx:1.27";
    # Untyped, through easykubenix's option, after kubenix rendered.
    kubernetes.objects.web.Deployment.nginx.spec.replicas = lib.mkForce 3;
  };
in
{
  # Cost: easykubenix alone, then with the bridge imported and no instance.
  # Both also run with `kubenixSrc = null`.
  without = generated [ plain ];
  unused = generated [
    plain
    bridge
  ];
  docsWithout = docs [ plain ];
  docsUnused = docs [
    plain
    bridge
  ];

  # An instance with `kubenixSrc = null`: the error names the argument.
  missingSrc = generated [
    bridge
    { kubenix.kubernetes.resources.configMaps.x.data.a = "1"; }
  ];

  instance =
    let
      c =
        (ekn [
          bridge
          instance
          patches
        ]).config.kubernetes;
      deployment = c.objects.web.Deployment.nginx;
    in
    {
      keys = lib.mapAttrs (_: lib.attrNames) c.objects;
      inherit (deployment.spec) replicas;
      containers = map (x: "${x.name}=${x.image}") deployment.spec.template.spec.containers;
      annotations = deployment.metadata.annotations;
      generated = lib.length c.generated;
    };
}
.${case}
