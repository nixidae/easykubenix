# kubenix modules in their own evaluation, imported like a Helm chart.
#
#   nix eval --impure --json --expr \
#     'import ./design/kinds/kubenix.nix { kubenix = <kubenix checkout>; }'
{ kubenix }:
let
  sources = import ../../nix/sources.nix;
  pkgs = import sources.nixpkgs { };
  lib = pkgs.lib.extend (import ../../easykubenix/lib/default.nix);
  kubeValueType = import ../../easykubenix/lib/kubeValueType.nix { inherit lib; };
  prototype = import ./kinds.nix { inherit lib kubeValueType; };

  # kubenix's own entry point, without its flake-compat wrapper.
  kubenixEval = import "${kubenix}/lib/eval-modules.nix" { inherit pkgs; };
  kubenixConfig =
    (kubenixEval {
      modules = [
        "${kubenix}/docs/content/examples/deployment/module.nix"
        "${kubenix}/docs/content/examples/custom-resources/module.nix"
        {
          kubenix.project = "compat";
          kubernetes.version = "1.28";
          kubernetes.namespace = "web";
        }
      ];
    }).config.kubernetes;

  # kubenix's `customTypes` say what the cluster serves, so they become
  # APIResource records. `name` (the plural) is optional in kubenix.
  customResources = lib.mapAttrs' (
    _: t:
    let
      plural = if t.name != null then t.name else t.attrName;
    in
    lib.nameValuePair "${plural}.${t.group}" (
      lib.mapAttrs (_: lib.mkDefault) {
        name = plural;
        namespaced = true;
        inherit (t) group version kind;
      }
    )
  ) kubenixConfig.customTypes;

  config =
    (lib.evalModules {
      modules = [
        prototype.module
        (
          { config, ... }:
          let
            imported = prototype.importObjects config.kubernetes kubenixConfig.objects;
          in
          {
            kubernetes.apiResources = lib.mkMerge [
              (prototype.apiResourceList ../../easykubenix/apiResources/v1.33.json)
              customResources
            ];
            kubernetes.objects = imported.objects;
          }
        )
        # An easykubenix patch on a kubenix object.
        { kubernetes.objects.web.Deployment.nginx.spec.replicas = lib.mkForce 3; }
      ];
    }).config.kubernetes;
in
{
  keys = lib.mapAttrs (_: lib.attrNames) config.objects;
  generated = map (
    o: "${o.apiVersion} ${o.kind} ${o.metadata.namespace or "-"}/${o.metadata.name}"
  ) config.generated;
  replicas = config.objects.web.Deployment.nginx.spec.replicas;
}
