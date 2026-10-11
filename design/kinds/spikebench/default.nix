# One workload through one easykubenix checkout. Both arms take the same
# `sources` and `pkgs`, so they differ in their own tree only. Run by run.py.
{
  # Path of the easykubenix checkout under test.
  arm,
  # "objects": N hand-written Deployments under a bare kind.
  # "crds": a YAML stream of N CRDs, each with a schema, and one custom
  # resource per CRD, through `importyaml`.
  workload,
  n,
  # "length" for timing; "hash" to check that both arms render the same.
  output ? "length",
}:
let
  sources = import ../../../nix/sources.nix;
  pkgs = import sources.nixpkgs { };
  inherit (pkgs) lib;

  deployment = i: {
    metadata.labels.app = "app${toString i}";
    spec = {
      replicas = 2;
      selector.matchLabels.app = "app${toString i}";
      template = {
        metadata.labels.app = "app${toString i}";
        spec.containers = [
          {
            name = "main";
            image = "nginx:1.27";
            ports = [ { containerPort = 80; } ];
            env = [
              {
                name = "A";
                value = "x";
              }
            ];
          }
        ];
      };
    };
  };

  crd = i: {
    apiVersion = "apiextensions.k8s.io/v1";
    kind = "CustomResourceDefinition";
    metadata.name = "widget${toString i}s.bench${toString i}.example.com";
    spec = {
      group = "bench${toString i}.example.com";
      scope = "Namespaced";
      names = {
        kind = "Widget${toString i}";
        plural = "widget${toString i}s";
        singular = "widget${toString i}";
      };
      versions = [
        {
          name = "v1";
          served = true;
          storage = true;
          schema.openAPIV3Schema = {
            type = "object";
            properties.spec = {
              type = "object";
              properties = lib.genAttrs (map (j: "field${toString j}") (lib.range 1 20)) (_: {
                type = "string";
              });
            };
          };
        }
      ];
    };
  };
  customResource = i: {
    apiVersion = "bench${toString i}.example.com/v1";
    kind = "Widget${toString i}";
    metadata = {
      name = "w${toString i}";
      namespace = "bench";
    };
    spec = lib.genAttrs (map (j: "field${toString j}") (lib.range 1 10)) (j: j);
  };
  range = lib.range 1 n;
  stream = pkgs.writeText "bench-${toString n}.yaml" (
    lib.concatMapStringsSep "\n---\n" builtins.toJSON (map crd range ++ map customResource range)
  );

  modules = {
    objects = [
      {
        kubernetes.objects.default.Deployment = lib.listToAttrs (
          map (i: lib.nameValuePair "app${toString i}" (deployment i)) range
        );
      }
    ];
    crds = [ { importyaml.bench.src = stream; } ];
  };

  # A path, not "${arm}/default.nix": interpolation copies the whole checkout
  # into the store on every run, and the two arms differ in size.
  ekn = import (arm + "/default.nix") {
    inherit sources pkgs;
    modules = modules.${workload};
  };
  json = builtins.toJSON ekn.config.kubernetes.generated;
in
if output == "hash" then builtins.hashString "sha256" json else builtins.stringLength json
