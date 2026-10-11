# kubenix's examples, unchanged, inside one easykubenix evaluation.
#
#   nix eval --impure --json --expr \
#     'import ./design/kinds/kubenix-wrapped.nix { kubenix = <kubenix checkout>; }'
{ kubenix }:
let
  lib = (import "${(import ../../nix/sources.nix).nixpkgs}/lib").extend (
    import ../../easykubenix/lib/default.nix
  );
  kubeValueType = import ../../easykubenix/lib/kubeValueType.nix { inherit lib; };
  prototype = import ./kinds.nix { inherit lib kubeValueType; };
  bridge = import ./kubenix-wrap.nix { inherit lib kubeValueType prototype; };
  shipped = ../../easykubenix/apiResources/v1.33.json;

  config =
    (lib.evalModules {
      modules = [
        prototype.module
        bridge.module
        { kubernetes.apiResources = prototype.apiResourceList shipped; }
        (bridge.wrap "${kubenix}/docs/content/examples/deployment/module.nix")
        (bridge.wrap "${kubenix}/docs/content/examples/custom-resources/module.nix")
        (bridge.wrap {
          kubernetes.namespace = "web";
          kubenix.project = "compat";
        })
        # Patches from easykubenix: one through kubenix's path, before the
        # list conversion, and one through easykubenix's own.
        {
          kubenix.resources.deployments.nginx.spec.template.spec.containers.nginx.image =
            lib.mkForce "nginx:1.27";
        }
        { kubernetes.objects.web.Deployment.nginx.spec.replicas = lib.mkForce 3; }
      ];
    }).config;

  # kubenix's own attribute names for v1.33, against the ones computed here.
  generated = builtins.readFile "${kubenix}/modules/generated/v1.33.nix";
  kubenixNames = lib.unique (
    map lib.head (
      lib.filter (m: m != null) (
        map (builtins.match "[[:space:]]*attrName = \"([^\"]+)\";.*") (lib.splitString "\n" generated)
      )
    )
  );
  computedNames = lib.unique (
    map (r: bridge.attrNameOf r.name r.kind) (lib.attrValues config.kubernetes.apiResources)
  );
in
{
  keys = lib.mapAttrs (_: lib.attrNames) config.kubernetes.objects;
  generated = map (
    o: "${o.apiVersion} ${o.kind} ${o.metadata.namespace or "-"}/${o.metadata.name}"
  ) config.kubernetes.generated;
  deployment = {
    inherit (config.kubernetes.objects.web.Deployment.nginx.spec) replicas;
    containers = config.kubernetes.objects.web.Deployment.nginx.spec.template.spec.containers;
  };
  attrNames = {
    computedNotInKubenix = lib.subtractLists kubenixNames computedNames;
    kubenixCount = lib.length kubenixNames;
  };
}
