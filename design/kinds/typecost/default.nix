# The same N Deployments through kubenix's generated v1.33 options tree and
# through easykubenix's `kubeValueType`. Run by ./run.sh.
{
  # "kubenix": kubenix's `config.kubernetes.objects`.
  # "ekn-objects": easykubenix's `config.kubernetes.objects`, the type alone.
  # "ekn-generated": easykubenix's `config.kubernetes.generated`, the pipeline.
  engine,
  n,
  kubenix,
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
        spec = {
          containers = [
            {
              name = "main";
              image = "nginx:1.27";
              ports = [
                {
                  name = "http";
                  containerPort = 80;
                }
              ];
              env = [
                {
                  name = "A";
                  value = "x";
                }
                {
                  name = "B";
                  value = "y";
                }
              ];
              resources.limits = {
                cpu = "100m";
                memory = "128Mi";
              };
              volumeMounts = [
                {
                  name = "data";
                  mountPath = "/data";
                }
              ];
            }
          ];
          volumes = [
            {
              name = "data";
              emptyDir = { };
            }
          ];
        };
      };
    };
  };
  objects = lib.listToAttrs (
    map (i: lib.nameValuePair "app${toString i}" (deployment i)) (lib.range 1 n)
  );

  kubenixEval = import "${kubenix}/lib/eval-modules.nix" { inherit pkgs; };
  ekn = modules: (import ../../../default.nix { inherit pkgs modules; }).config.kubernetes;

  result =
    {
      kubenix =
        (kubenixEval {
          modules = [
            (
              { kubenix, ... }:
              {
                imports = [ kubenix.modules.k8s ];
                kubenix.project = "bench";
                kubernetes.version = "1.33";
                kubernetes.namespace = "default";
                kubernetes.resources.deployments = objects;
              }
            )
          ];
        }).config.kubernetes.objects;
      ekn-objects = (ekn [ { kubernetes.objects.default.Deployment = objects; } ]).objects;
      ekn-generated = (ekn [ { kubernetes.objects.default.Deployment = objects; } ]).generated;
    }
    .${engine};
in
builtins.stringLength (builtins.toJSON result)
