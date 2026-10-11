{
  case ? "aliases",
}:
let
  lib = (import "${(import ../../nix/sources.nix).nixpkgs}/lib").extend (
    import ../../easykubenix/lib/default.nix
  );
  kubeValueType = import ../../easykubenix/lib/kubeValueType.nix { inherit lib; };
  prototype = import ./kinds.nix { inherit lib kubeValueType; };

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

  # What two Helm charts would emit: each one's CRD and one object. The CNPG
  # CRD stores v1beta1 and serves v1 too, so discovery prefers v1.
  chartObjects = [
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
    (crd "k8s.mariadb.com" [
      {
        name = "v1alpha1";
        served = true;
        storage = true;
      }
    ])
    (object "postgresql.cnpg.io/v1" { instances = 3; })
    (object "k8s.mariadb.com/v1alpha1" { replicas = 2; })
  ];

  shipped = prototype.apiResourceList ../../easykubenix/apiResources/v1.33.json;

  base =
    { config, ... }:
    let
      imported = prototype.importObjects config.kubernetes chartObjects;
    in
    {
      kubernetes.apiResources = lib.mkMerge [
        shipped
        imported.apiResources
      ];
      kubernetes.objects = imported.objects;
    };

  aliases.kubernetes.kindAliases = {
    PGCluster = "Cluster.postgresql.cnpg.io";
    MariaCluster = "Cluster.k8s.mariadb.com";
  };

  cases = {
    aliases = [
      aliases
      {
        # A patch through the alias merges onto the imported object.
        kubernetes.objects.x.PGCluster.db.spec.instances = lib.mkForce 5;
        kubernetes.objects.x.PGCluster.other = { };
        kubernetes.objects.x.Deployment.web = { };
        # Core v1 and events.k8s.io both serve Event; the core group wins.
        kubernetes.objects.x.Event.e = { };
        kubernetes.objects.x.Foo.bar.apiVersion = "foo.io/v1";
      }
    ];
    # No aliases: imports land at GroupKind strings, and so can a hand-written
    # object.
    qualified = [
      {
        kubernetes.objects.x."Cluster.k8s.mariadb.com".db2 = { };
      }
    ];
    # The GroupKind string for an object the alias already holds.
    collision = [
      aliases
      { kubernetes.objects.x."Cluster.postgresql.cnpg.io".db = { }; }
    ];
    # Two attribute names, one `metadata.name`.
    renamed = [
      {
        kubernetes.objects.x.Deployment.web = { };
        kubernetes.objects.x.Deployment.web2.metadata.name = "web";
      }
    ];
    # A bare kind two non-core groups serve.
    ambiguous = [ { kubernetes.objects.x.Cluster.db3 = { }; } ];
  };

  config =
    (lib.evalModules {
      modules = [
        prototype.module
        base
      ]
      ++ cases.${case};
    }).config.kubernetes;
in
{
  keys = lib.attrNames config.objects.x;
  generated = map (
    o: "${o.apiVersion} ${o.kind} ${o.metadata.name} ${builtins.toJSON (o.spec or { })}"
  ) config.generated;
}
