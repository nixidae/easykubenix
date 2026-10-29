# easykubenix's outputs, in the schema of nixidae/nixidae#1103. The evaluation
# of a configuration is `lib.easykubenix`, in ./eval.nix.
{
  sources ? import ./nix/sources.nix,
  system ? builtins.currentSystem,
  pkgs ? import sources.nixpkgs {
    inherit system;
    config.allowUnfree = true;
  },
  nixLane ? "stable",
}:
let
  easykubenix =
    args:
    import ./eval.nix (
      {
        inherit
          sources
          system
          pkgs
          nixLane
          ;
      }
      // args
    );
  demo = easykubenix { };

  # The options of an empty configuration: every module easykubenix declares.
  optionsReference =
    import ./nix/shared/options-reference.nix
      {
        inherit (pkgs) lib;
        inherit pkgs;
      }
      {
        title = "Options";
        inherit (easykubenix { modules = [ ]; }) options;
        prefixes = [
          "deployment."
          "ekn."
          "gitOps."
          "helm."
          "importyaml"
          "kluctl."
          "kubernetes."
          "validation."
        ];
        root = ./.;
        repository = "nixidae/easykubenix";
      };
  outputs = import ./nix { inherit sources system pkgs; };
in
{
  packages = {
    inherit (demo.passthru) ekn;
    default = demo.passthru.ekn;
  };
  lib = { inherit easykubenix; };
  nixosModules = { };
  easykubenixModules = { };
  inherit (outputs) checks shell;
  docs.default = pkgs.python3Packages.callPackage ./nix/docs.nix {
    inherit (demo.passthru) nixvenv;
    inherit optionsReference;
  };
  ciTestWorkflows.tests = import ./ci/workflows/tests.nix;

  # Not public: the names may change without notice.
  internal = outputs // {
    inherit (demo.passthru)
      lanes
      eknDevEnv
      nixvenv
      huggorm-bindings
      ;
  };
}
