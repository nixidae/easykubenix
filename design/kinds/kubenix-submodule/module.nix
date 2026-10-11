# kubenix as a submodule of easykubenix: `kubenix.instances.<name>` is a whole
# kubenix configuration, typed by kubenix's own modules. Its
# `kubernetes.objects` become easykubenix `kubernetes.objects`.
#
# `kubenixSrc` is a special argument: the kubenix source, or null. It stands in
# for a `kubenixSrc ? null` argument of easykubenix's default.nix, which would
# forward it to every instance with the rest of its special arguments.
{
  config,
  lib,
  pkgs,
  kubenixSrc,
  ...
}:
let
  src =
    if kubenixSrc != null then
      kubenixSrc
    else
      throw ''
        `kubenix.instances' is set, but easykubenix was called without
        `kubenixSrc'. Pass the kubenix source:

          import easykubenix { kubenixSrc = <kubenix checkout>; ... }
      '';

  # What kubenix's lib/eval-modules.nix passes its modules.
  kubenixLib = pkgs.lib.extend (lib: _: import "${src}/lib/upstreamables.nix" { inherit lib pkgs; });
  kubenixArg = {
    lib = import "${src}/lib" {
      inherit pkgs;
      inherit (pkgs) lib;
    };
    modules = import "${src}/modules";
    evalModules = import "${src}/lib/eval-modules.nix" { inherit pkgs; };
  };

  instanceType = lib.types.submoduleWith {
    modules = [
      kubenixArg.modules.k8s
      { kubenix.project = lib.mkDefault "easykubenix"; }
    ];
    specialArgs = {
      kubenix = kubenixArg;
      inherit pkgs;
      lib = kubenixLib;
      # Read-only access to the easykubenix configuration around the instance.
      eknConfig = config;
    };
  };

  objects = lib.concatLists (
    lib.mapAttrsToList (_: instance: instance.kubernetes.objects) config.kubenix.instances
  );
in
{
  options.kubenix.instances = lib.mkOption {
    type = lib.types.attrsOf instanceType;
    default = { };
    # Documentation walks a visible option's sub-options. kubenix's
    # generated tree is thousands of options per Kubernetes version.
    visible = "shallow";
    description = "kubenix configurations, each evaluated with kubenix's own modules.";
  };

  config.kubernetes.objects = lib.mkMerge (
    map (object: {
      ${object.metadata.namespace or "none"}.${object.kind}.${object.metadata.name} = object;
    }) objects
  );
}
