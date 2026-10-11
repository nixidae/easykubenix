# Which reads between easykubenix and a kubenix instance evaluate, and which
# recurse. Each case prints the value it reads, or fails.
#
#   nix eval --impure --json --expr \
#     'import ./design/kinds/kubenix-submodule/cycles.nix { kubenixSrc = <checkout>; case = "<case>"; }'
{ kubenixSrc, case }:
let
  sources = import ../../../nix/sources.nix;
  pkgs = import sources.nixpkgs { };
  inherit (pkgs) lib;

  # An easykubenix object for kubenix to read.
  eknSide = {
    kubernetes.objects.default.ConfigMap.source.data.x = "from-ekn";
    kubernetes.objects.none.Namespace.app = { };
  };
  # A kubenix object for easykubenix to read.
  kubenixSide = {
    kubenix.instances.web.kubernetes.resources.configMaps.k.data.a = "from-kubenix";
  };

  cases = {
    # (a) A kubenix value copies a field of an easykubenix object.
    eknValue = {
      modules = [
        eknSide
        (
          { ... }:
          {
            kubenix.instances.web =
              { eknConfig, ... }:
              {
                kubernetes.resources.configMaps.copy.data.x =
                  eknConfig.kubernetes.objects.default.ConfigMap.source.data.x;
              };
          }
        )
      ];
      read = c: c.kubernetes.objects.none.ConfigMap.copy.data.x;
    };
    # (b) A kubenix resource exists only when an easykubenix object does.
    eknExistence = {
      modules = [
        eknSide
        {
          kubenix.instances.web =
            { eknConfig, ... }:
            {
              kubernetes.resources.configMaps = lib.mkIf (eknConfig.kubernetes.objects.default ? ConfigMap) {
                gated.data.x = "1";
              };
            };
        }
      ];
      read = c: lib.attrNames c.kubernetes.objects;
    };
    # (c) kubenix reads easykubenix's output, which holds kubenix's own.
    eknGenerated = {
      modules = [
        eknSide
        {
          kubenix.instances.web =
            { eknConfig, ... }:
            {
              kubernetes.resources.configMaps.count.data.n = toString (lib.length eknConfig.kubernetes.generated);
            };
        }
      ];
      read = c: c.kubernetes.objects.none.ConfigMap.count.data.n;
    };
    # (d) An easykubenix object reads a kubenix value.
    fromKubenix = {
      modules = [
        kubenixSide
        (
          { config, ... }:
          {
            kubernetes.objects.default.ConfigMap.reader.data.a =
              config.kubenix.instances.web.kubernetes.api.resources.core.v1.ConfigMap.k.data.a;
          }
        )
      ];
      read = c: c.kubernetes.objects.default.ConfigMap.reader.data.a;
    };
    # (e) Both directions at once, on different objects.
    bothWays = {
      modules = cases.eknValue.modules ++ cases.fromKubenix.modules;
      read = c: [
        (cases.eknValue.read c)
        (cases.fromKubenix.read c)
      ];
    };
    # (f) kubenix's namespace, which decides where its objects land, comes
    # from an easykubenix object.
    eknNamespace = {
      modules = [
        eknSide
        {
          kubenix.instances.web =
            { eknConfig, ... }:
            {
              kubernetes.namespace = eknConfig.kubernetes.objects.none.Namespace.app.metadata.name;
              kubernetes.resources.configMaps.placed.data.x = "1";
            };
        }
      ];
      read = c: lib.attrNames c.kubernetes.objects;
    };
  };

  selected = cases.${case};
  config =
    (import ../../../default.nix {
      inherit pkgs;
      modules = [ ./module.nix ] ++ selected.modules;
      specialArgs = { inherit kubenixSrc; };
    }).config;
in
selected.read config
