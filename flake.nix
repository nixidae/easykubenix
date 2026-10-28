# The public surface of this repository, for a consumer who uses flakes.
#
# **This is not how the repository builds.** `default.nix` is, and nixidae
# (https://github.com/nixidae/nixidae), which this repository is exported
# from, hands it every source. This file exists because flakes have the
# market share: it lets somebody write an input for this repository and get a
# curated set of outputs, rather than nothing.
#
# So it holds no logic. It names what is public and calls `default.nix`, and
# a change to how anything is built happens there.
#
# Two inputs, and neither duplicates a pin nixidae keeps.
#
#   nixpkgs   The consumer's, and the point of the exercise. It is handed to
#             nixidae in place of the revision its flake.lock names, so
#             `inputs.<this>.inputs.nixpkgs.follows = "nixpkgs"` does what a
#             flake user expects it to.
#
#   nixidae   Which nixidae commit, and nothing else. `flake.lock` pins it,
#             and lock-subtrees writes that lock. Every other source comes
#             from that commit's own flake.lock.
{
  description = "Like kubenix, but easier";

  inputs = {
    nixpkgs.url = "github:nixos/nixpkgs/nixpkgs-unstable";
    nixidae = {
      url = "github:nixidae/nixidae";
      flake = false;
    };
  };

  outputs =
    {
      self,
      nixpkgs,
      nixidae,
    }:
    let
      inherit (nixpkgs) lib;
      forEachSystem = lib.genAttrs lib.systems.flakeExposed;

      # nixidae's own set, with the consumer's nixpkgs in place of the one it
      # names.
      sources = import "${nixidae}/nix/wire.nix" {
        overrides.nixpkgs = nixpkgs.outPath;
      };

      pkgsFor =
        system:
        import nixpkgs {
          inherit system;
          config.allowUnfree = true;
        };

      each = forEachSystem (
        system:
        import ./. {
          inherit sources system;
          pkgs = pkgsFor system;
        }
      );
    in
    {
      packages = forEachSystem (system: {
        inherit (each.${system}.passthru)
          nanopynix
          huggorm-bindings
          easykubenix-docs
          # This repository's own build of the CLI, from `ekn/`. See
          # `eknCli` in default.nix.
          ekn
          ;
        default = each.${system}.passthru.ekn;
      });

      # Re-exported, not defined here. These are plain derivations built by
      # `nix build --file ./checks.nix all`, so the gates need no flake
      # command -- see nix/default.nix and docs/examples/default.nix.
      checks = forEachSystem (
        system:
        (import ./nix {
          inherit sources system;
          pkgs = pkgsFor system;
        }).checks
      );

      # The entry point itself, which is how a consumer evaluates a
      # configuration of their own.
      lib.easykubenix = import ./default.nix;

      devShells = forEachSystem (
        system:
        let
          pkgs = pkgsFor system;
        in
        {
          default = pkgs.python3Packages.callPackage ./nix/shell.nix {
            inherit (pkgs) ruff;
            inherit (each.${system}.passthru) eknDevEnv nixvenv;
          };
        }
      );
    };
}
