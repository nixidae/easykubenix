let
  sources = import ../nix/sources.nix;
  pkgs = import sources.nixpkgs { };
  inherit (pkgs) lib;
  job = hook: image: {
    apiVersion = "batch/v1";
    metadata.annotations."helm.sh/hook" = hook;
    spec.template.spec = {
      restartPolicy = "Never";
      containers = [
        {
          name = "main";
          inherit image;
        }
      ];
    };
  };
  generated =
    module:
    ((import ../. { }).lib.easykubenix {
      inherit pkgs;
      modules = [ module ];
    }).config.kubernetes.generated;
  render =
    image:
    generated {
      kubernetes.objects.hooks = {
        Job.migrate = job "pre-install, pre-upgrade" image;
        Job.cleanup = job "pre-delete" image;
        Job.plain = {
          apiVersion = "batch/v1";
          inherit (job "" image) spec;
        };
        ConfigMap.helper.metadata.annotations."helm.sh/hook" = "post-install";
        Pod.probe.metadata.annotations."helm.sh/hook" = "test";
        Job.${lib.concatStrings (lib.replicate 70 "n")} = job "post-upgrade" image;
      };
    };
  off = generated {
    kubernetes.helmHooks.enable = false;
    kubernetes.objects.hooks.Job.migrate = job "pre-install" "a";
  };
  # A transformer turns on the marker pass; the hash must still see one spec.
  withTransformer =
    image:
    generated {
      kubernetes.transformers = [ (object: object) ];
      kubernetes.objects.hooks.Job.migrate = job "pre-install" image;
    };

  namesOf =
    kind: objects:
    map (object: object.metadata.name) (lib.filter (object: object.kind == kind) objects);
  migrateName = objects: lib.findFirst (lib.hasPrefix "migrate-") null (namesOf "Job" objects);
  a = render "a";
  b = render "b";
in
{
  changedSpecIsANewJob = migrateName a != null && migrateName a != migrateName b;
  sameSpecIsTheSameJob = migrateName a != null && migrateName a == migrateName (render "a");
  plainJobKeepsItsName = lib.elem "plain" (namesOf "Job" a);
  hookOtherThanJobKeepsItsName = lib.elem "helper" (namesOf "ConfigMap" a);
  deleteHookIsDropped = !lib.any (lib.hasPrefix "cleanup") (namesOf "Job" a);
  testHookIsDropped = namesOf "Pod" a == [ ];
  nameFitsALabel =
    lib.all (name: builtins.stringLength name <= 63) (namesOf "Job" a)
    && lib.any (lib.hasPrefix "nnnn") (namesOf "Job" a);
  disabledLeavesTheName = namesOf "Job" off == [ "migrate" ];
  transformerKeepsTheHash =
    let
      plain = migrateName (generated {
        kubernetes.objects.hooks.Job.migrate = job "pre-install" "a";
      });
    in
    plain != null && migrateName (withTransformer "a") == plain;
}
