# The design's cases against the spike in easykubenix itself.
#
#   nix eval --impure --json --expr 'import ./design/kinds/spikebench/cases.nix { case = "<case>"; }'
{ case }:
let
  sources = import ../../../nix/sources.nix;
  pkgs = import sources.nixpkgs { };
  inherit (pkgs) lib;

  crd = group: versions: {
    apiVersion = "apiextensions.k8s.io/v1";
    kind = "CustomResourceDefinition";
    metadata.name = "clusters.${group}";
    spec = {
      inherit group versions;
      names = {
        kind = "Cluster";
        plural = "clusters";
        singular = "cluster";
      };
      scope = "Namespaced";
    };
  };
  object = apiVersion: spec: {
    inherit apiVersion spec;
    kind = "Cluster";
    metadata = {
      name = "db";
      namespace = "x";
    };
  };
  stream =
    name: docs: pkgs.writeText "${name}.yaml" (lib.concatMapStringsSep "\n---\n" builtins.toJSON docs);

  # Two charts, each shipping a `Cluster` CRD and a `Cluster db`. The CNPG CRD
  # stores v1beta1 and serves v1, so discovery prefers v1.
  charts = {
    importyaml.cnpg.src = stream "cnpg" [
      (crd "postgresql.cnpg.io" [
        {
          name = "v1beta1";
          served = true;
          storage = true;
        }
        {
          name = "v1";
          served = true;
        }
      ])
      (object "postgresql.cnpg.io/v1" { instances = 3; })
    ];
    importyaml.mariadb.src = stream "mariadb" [
      (crd "k8s.mariadb.com" [
        {
          name = "v1alpha1";
          served = true;
          storage = true;
        }
      ])
      (object "k8s.mariadb.com/v1alpha1" { replicas = 2; })
    ];
  };
  aliases.kubernetes.kindAliases = {
    PGCluster = "Cluster.postgresql.cnpg.io";
    MariaCluster = "Cluster.k8s.mariadb.com";
  };

  cases = {
    # No declaration: the imports land at their GroupKind keys.
    qualified = [ charts ];
    aliases = [
      charts
      aliases
      {
        kubernetes.objects.x.PGCluster.db.spec.instances = lib.mkForce 5;
        kubernetes.objects.x.PGCluster.other = { };
        kubernetes.objects.x.Event.e = { };
      }
    ];
    collision = [
      charts
      aliases
      { kubernetes.objects.x."Cluster.postgresql.cnpg.io".db = { }; }
    ];
    renamed = [
      {
        kubernetes.objects.x.Deployment.web = { };
        kubernetes.objects.x.Deployment.web2.metadata.name = "web";
      }
    ];
    ambiguous = [
      charts
      { kubernetes.objects.x.Cluster.db3 = { }; }
    ];
  };

  ekn = import ../../../default.nix {
    inherit sources pkgs;
    modules = cases.${case};
  };
  inherit (ekn.config) kubernetes;
in
{
  keys = lib.mapAttrs (_: lib.attrNames) kubernetes.objects;
  generated = map (
    o:
    "${o.apiVersion} ${o.kind} ${o.metadata.namespace or "-"}/${o.metadata.name} ${
      builtins.toJSON (o.spec or { })
    }"
  ) kubernetes.generated;
}
