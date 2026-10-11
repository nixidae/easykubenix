{
  # "objects": force every object; "generated": the same, through the
  # identity check.
  output,
  n ? 2000,
}:
let
  lib = (import "${(import ../../nix/sources.nix).nixpkgs}/lib").extend (
    import ../../easykubenix/lib/default.nix
  );
  kubeValueType = import ../../easykubenix/lib/kubeValueType.nix { inherit lib; };
  prototype = import ./kinds.nix { inherit lib kubeValueType; };
  spec = i: {
    instances = 3;
    storage = {
      size = "${toString i}Gi";
      class = "ceph";
    };
    postgresql.parameters = lib.genAttrs (map (j: "p${toString j}") (lib.range 1 12)) (j: j);
    monitoring.enabled = true;
  };
  objects = lib.listToAttrs (
    map (i: lib.nameValuePair "db${toString i}" { spec = spec i; }) (lib.range 1 n)
  );
  config =
    (lib.evalModules {
      modules = [
        prototype.module
        {
          kubernetes.apiResources."clusters.postgresql.cnpg.io" = {
            name = "clusters";
            namespaced = true;
            group = "postgresql.cnpg.io";
            version = "v1";
            kind = "Cluster";
          };
          kubernetes.kindAliases.PGCluster = "Cluster.postgresql.cnpg.io";
          kubernetes.objects.x.PGCluster = objects;
        }
      ];
    }).config.kubernetes;
in
builtins.stringLength (builtins.toJSON config.${output})
