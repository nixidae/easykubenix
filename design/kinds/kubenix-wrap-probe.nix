# The rewriting route's two edge cases. `read` passes; `mixed` fails to merge,
# because nothing without kubenix's types turns the attribute set into a list.
#
#   nix eval --impure --json --expr 'import ./design/kinds/kubenix-wrap-probe.nix { case = "mixed"; }'
{ case }:
let
  root = ../..;
  lib = (import "${(import (root + "/nix/sources.nix")).nixpkgs}/lib").extend (
    import (root + "/easykubenix/lib/default.nix")
  );
  kubeValueType = import (root + "/easykubenix/lib/kubeValueType.nix") { inherit lib; };
  prototype = import (root + "/design/kinds/kinds.nix") { inherit lib kubeValueType; };
  bridge = import (root + "/design/kinds/kubenix-wrap.nix") { inherit lib kubeValueType prototype; };
  modules = {
    # A kubenix module that reads another resource through config.kubernetes.
    read = [
      (bridge.wrap (
        { config, ... }:
        {
          kubernetes.resources.configMaps.cfg.data.a = "1";
          kubernetes.resources.deployments.web.metadata.annotations.cm =
            config.kubernetes.resources.configMaps.cfg.metadata.name;
        }
      ))
    ];
    # One module writes the list form, another the attribute-set form.
    mixed = [
      (bridge.wrap {
        kubernetes.resources.deployments.web.spec.template.spec.containers = [
          {
            name = "a";
            image = "x";
          }
        ];
      })
      (bridge.wrap { kubernetes.resources.deployments.web.spec.template.spec.containers.b.image = "y"; })
    ];
  };
  config =
    (lib.evalModules {
      modules = [
        prototype.module
        bridge.module
        {
          kubernetes.apiResources = prototype.apiResourceList (root + "/easykubenix/apiResources/v1.33.json");
        }
      ]
      ++ modules.${case};
    }).config;
in
config.kubernetes.generated
