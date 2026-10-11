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

  # kubenix's own `moduleToAttrs` (modules/k8s.nix): drop nulls and names that
  # start with `_`, except in a ConfigMap's `data` and `binaryData`.
  moduleToAttrs =
    type: path: value:
    if lib.isAttrs value then
      let
        keepUnderscore =
          type.group == "core"
          && type.version == "v1"
          && type.kind == "ConfigMap"
          && lib.elem path [
            [ "data" ]
            [ "binaryData" ]
          ];
      in
      lib.mapAttrs (name: moduleToAttrs type (path ++ [ name ])) (
        lib.filterAttrs (name: v: v != null && (keepUnderscore || !lib.hasPrefix "_" name)) value
      )
    else if lib.isList value then
      map (moduleToAttrs type path) value
    else
      value;

  # The key of each object must not force its values. A kubenix value may read
  # `eknConfig.kubernetes.objects`, and the keys below are part of that
  # option's own definitions, so a forced value is infinite recursion. Two
  # places force values:
  #
  # - kubenix's `kubernetes.objects`, whose `apply` runs `unique`, which
  #   compares whole objects. Hence the typed `api.resources` tree instead.
  # - `moduleToAttrs`, which drops nulls and so forces each field. A field
  #   typed `attrsOf` (a ConfigMap's `data`) cannot list its keys without
  #   forcing each value, since any value may be `mkIf false`. Hence the
  #   namespace and name come from the typed `metadata`, and `moduleToAttrs`
  #   runs inside the value.
  #
  # kubenix's `enableHashedNames` puts a hash of the content into a
  # ConfigMap's or Secret's name, so with it a name depends on every value.
  objects = lib.concatLists (
    lib.mapAttrsToList (
      _: instance:
      let
        k = instance.kubernetes;
      in
      lib.concatLists (
        lib.mapAttrsToList (
          _: type:
          let
            resources = k.api.resources.${type.group}.${type.version}.${type.kind};
            hashed =
              if
                k.enableHashedNames
                && lib.elem type.kind [
                  "ConfigMap"
                  "Secret"
                ]
              then
                kubenixArg.lib.k8s.injectHashedNames resources
              else
                resources;
          in
          lib.mapAttrsToList (_: typed: {
            namespace = typed.metadata.namespace or null;
            inherit (type) kind;
            inherit (typed.metadata) name;
            value = moduleToAttrs type [ ] typed;
          }) hashed
        ) k.api.types
      )
    ) config.kubenix.instances
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
      ${if object.namespace == null then "none" else object.namespace}.${object.kind}.${object.name} =
        object.value;
    }) objects
  );
}
