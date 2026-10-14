# The test workflow, as a value. nixidae renders it to
# `.github/workflows/easykubenix-tests.yml`, with easykubenix as the working
# directory of every step. Tests run only in nixidae; easykubenix's own
# repository runs `.github/workflows/pages.yml`. nixidae/nixidae#4.
{
  name = "tests";

  on = {
    pull_request = null;
    push = null;
  };

  jobs.checks = {
    runs-on = "ubuntu-24.04";
    steps = [
      { uses = "actions/checkout@v4"; }
      # cachix/install-nix-action, which installs a daemon, and not
      # nixbuild/nix-quick-install-action, which does not.
      #
      # A daemon builds in a sandbox whose build directory is `/build`. A
      # single-user nix builds under the runner's TMPDIR, so a derivation
      # that names `/build` cannot create it and has no way to ask for
      # root. `nix/tofu` sets `HOME = "/build/home"` because tofu writes its
      # plugin cache there, so `checks.tofu-render` failed every run with
      #
      #     mkdir: cannot create directory '/build': Permission denied
      #
      # and took `checks.all` with it. Same class as nixkube's issue #23,
      # and the same fix.
      #
      # The substituters come with it. This job built every dependency from
      # source before, because nothing gave it a cache to read; a daemon
      # ignores substituters a user it does not trust asks for, which is
      # what `trusted-users` is for.
      {
        uses = "cachix/install-nix-action@master";
        "with".extra_nix_config = ''
          access-tokens = github.com=''${{ github.token }}
          experimental-features = nix-command flakes
          trusted-users = root runner
          trusted-public-keys = cache.nixos.org-1:6NCHdD59X431o0gWypbMrAURkbJ16ZPMQFGspcDShjY= lillecarl.cachix.org-1:NN/LLMg7mbyvZCu32Qlo8LpSHqNw7Rr3VBCEYQvRpT0=
          substituters = https://cache.nixos.org?priority=1 https://lillecarl.cachix.org?priority=2
        '';
      }
      # No flake command anywhere in this job. `checks.nix` and `shell.nix` are
      # plain imports, so every gate here also runs from a checkout with no
      # flake support -- which is the point. `all` covers every example, so a
      # new one is gated the moment it joins the list in
      # docs/examples/default.nix, and it covers the repository's own gates
      # (`ekn-sandbox`, `nixfmt`) from nix/default.nix.
      {
        name = "Build checks";
        run = "nix build --file ./checks.nix all --no-link --print-build-logs";
      }
      {
        name = "Validate against real kube-apiserver";
        run = "nix run --file ./nix packages.validationScript";
      }
      {
        name = "Run tests";
        run = "nix-shell --run 'python3 -m pytest -v --no-header'";
      }
      # `ekn/` is this repository's Python source, and nothing else lints or
      # type-checks it.
      {
        name = "Lint";
        run = "nix-shell --run 'ruff check --config ruff-strict.toml'";
      }
      {
        name = "Check Python formatting";
        run = "nix-shell --run 'ruff format --check'";
      }
      {
        name = "Check types";
        run = "nix-shell --run 'pyright ekn'";
      }
    ];
  };
}
