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
  # The shape before nixidae/nixidae#1103: a call with `modules` returns the
  # evaluation, as `lib.easykubenix` does.
  modules ? null,
  specialArgs ? { },
  debug ? null,
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
  outputs = import ./nix { inherit sources system pkgs; };
in
if modules != null then
  easykubenix { inherit modules specialArgs debug; }
else
  {
    packages = {
      inherit (demo.passthru) ekn;
      default = demo.passthru.ekn;
    };
    lib = { inherit easykubenix; };
    nixosModules = { };
    inherit (outputs) checks shell;
    docs.default = demo.passthru.easykubenix-docs;
    ciTestWorkflows.tests = import ./ci/workflows/tests.nix;

    # Not public: the names may change without notice.
    internal = outputs // {
      inherit (demo.passthru) lanes eknDevEnv nixvenv;
    };

    # nix/members.nix, the root, flake.nix and nix/default.nix read it.
    inherit (demo) passthru;
  }
