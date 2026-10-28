# The root module class: an environment's facts, its units and its
# generators. No class's options live here.
#
# A unit is one configuration of one class, as a submodule of this
# evaluation: `units.<name>.kubernetes`, `.tofu` or `.nixos`. One fixpoint,
# so a unit reads the root and its siblings through the `root` module
# argument -- a Kubernetes unit can name a tofu unit's `configFile`.
#
# Not `types.attrTag`: it hands the inner submodule the tag as `name`, and
# a unit has to know its own.
{
  config,
  ekn,
  lib,
  pkgs,
  ...
}:
let
  inherit (lib) mkOption types;
  cfg = config;
  checked = lib.asserts.checkAssertWarn config.assertions config.warnings;

  unitArgs = {
    root = config;
  };
  classes = {
    kubernetes = (ekn.lib.mkInstance { specialArgs = unitArgs; }).type;
    tofu =
      (ekn.lib.mkInstance {
        class = "tf";
        specialArgs = unitArgs;
      }).type;
    # A NixOS evaluation, as a generator returns it: what `nixosSystem`
    # returns, with `config` and `extendModules`. A value, not a submodule:
    # its own nixpkgs evaluated it.
    nixos = types.addCheck types.raw (v: v ? config.system.build.toplevel) // {
      description = "a NixOS evaluation";
    };
  };

  function = lib.mkOptionType {
    name = "function";
    description = "function";
    check = lib.isFunction;
    merge = lib.mergeOneOption;
  };

  /**
    A NixOS generator: a function from a node to its NixOS evaluation, as
    `nixosSystem` returns it (`config`, `extendModules`). The node is
    `{ name, labels ? { }, system ? ... }`, nixkube's NixOSRollout contract,
    which evaluates `(generator node).config.system.build.toplevel`. It
    reaches the modules as the `node` argument, next to `root`.

    `nixpkgs` is where the NixOS modules come from, as NixOS containers do,
    so a host can run another NixOS release than easykubenix. A flake input
    names the system after its date and revision, as `nixosSystem` does.
  */
  nixosGenerator =
    {
      nixpkgs ? pkgs.path,
      specialArgs ? { },
      modules,
    }:
    {
      name,
      labels ? { },
      system ? pkgs.stdenv.hostPlatform.system,
      ...
    }@node:
    (import (nixpkgs + "/nixos/lib/eval-config.nix") {
      system = null;
      lib =
        if nixpkgs ? lastModifiedDate then
          (import (nixpkgs + "/lib")).extend (import (nixpkgs + "/lib/flake-version-info.nix") nixpkgs)
        else
          import (nixpkgs + "/lib");
      modules = [ { nixpkgs.hostPlatform = lib.mkDefault system; } ] ++ modules;
      specialArgs =
        unitArgs
        // specialArgs
        // {
          node = node // {
            inherit labels system;
          };
        };
    });

  ofClass = class: lib.filterAttrs (_: unit: unit.${class} != null) cfg.units;
in
{
  _class = "root";

  options = {
    units = mkOption {
      default = { };
      description = ''
        The environment's configurations, one class each. Exactly one of
        `kubernetes`, `tofu` and `nixos` is set per unit.
      '';
      type = types.attrsOf (
        types.submodule (
          { name, ... }:
          {
            options = {
              kubernetes = mkOption {
                type = types.nullOr classes.kubernetes;
                default = null;
                description = "An easykubenix configuration: one cluster.";
              };
              tofu = mkOption {
                type = types.nullOr classes.tofu;
                default = null;
                description = "An OpenTofu configuration.";
              };
              nixos = mkOption {
                type = types.nullOr classes.nixos;
                default = null;
                description = "A host that ekn applies: usually a generator's result.";
                example = lib.literalExpression ''config.generators.rs501a { name = "cp1"; }'';
              };
              path = mkOption {
                type = types.str;
                default = name;
                description = "Where `ekn commit` writes a tofu unit's configuration.";
              };
              dependencies = mkOption {
                type = types.listOf types.str;
                default = [ ];
                description = "Tofu units `ekn tofu` applies before this one.";
              };
            };
          }
        )
      );
    };

    generators = mkOption {
      type = types.attrsOf function;
      default = { };
      description = ''
        Functions something else calls, never applied by ekn. A NixOS
        generator is what a NixOSRollout names; build one with the
        `nixosGenerator` module argument.
      '';
    };

    deployment = {
      tofuUnits = mkOption {
        type = types.anything;
        readOnly = true;
        description = ''
          The tofu units, in the shape `ekn tofu` reads from a Kubernetes
          root, behind this root's assertions.
        '';
      };
      nixosUnits = mkOption {
        type = types.anything;
        readOnly = true;
        description = "The NixOS units: each one's toplevel, behind this root's assertions.";
      };
    };
  };

  config = {
    _module.args.nixosGenerator = nixosGenerator;

    assertions = lib.mapAttrsToList (name: unit: {
      assertion =
        lib.length (
          lib.filter (v: v != null) [
            unit.kubernetes
            unit.tofu
            unit.nixos
          ]
        ) == 1;
      message = "units.${name}: set exactly one of kubernetes, tofu and nixos.";
    }) cfg.units;

    deployment.tofuUnits = lib.mapAttrs (
      name: unit:
      let
        tofu = unit.tofu.tofu;
      in
      {
        inherit name;
        target = { inherit (unit) path; };
        inherit (unit) dependencies;
        configFile = checked "${tofu.configFile}";
        jsonSchema = "${tofu.jsonSchema}";
        tofu = "${tofu.wrappedPackage}/bin/tofu";
      }
    ) (ofClass "tofu");

    deployment.nixosUnits = lib.mapAttrs (name: unit: {
      inherit name;
      inherit (unit.nixos.config.nixpkgs.hostPlatform) system;
      toplevel = checked "${unit.nixos.config.system.build.toplevel}";
    }) (ofClass "nixos");
  };
}
