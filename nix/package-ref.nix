{ pkgs, root }:
let
  inherit (pkgs) lib;
  preview = import ../easykubenix/lib/packageRef.nix { enabled = true; };
  normal = import ../easykubenix/lib/packageRef.nix { enabled = false; };
  poison = throw "Package references force their package argument.";
  path = "/nix/store/AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA-example";
  package =
    (builtins.derivation {
      name = "package-ref-check";
      system = pkgs.stdenv.hostPlatform.system;
      builder = "/unused";
    })
    // {
      meta.mainProgram = "actual-program";
    };
  instance = root.passthru.eval.extendModules {
    modules = [
      ({ ekn, ... }: {
        kubernetes.resources.demo.ConfigMap.package-ref.data.path =
          "${ekn.lib.packageRef "example" package}";
      })
    ];
  };
  tests = {
    interpolationIsLazy = "${preview "example" poison}" == path;
    getExeIsLazy = lib.getExe (preview "example" poison) == "${path}/bin/example";
    getExePrimeIsLazy = lib.getExe' (preview "example" poison) "other" == "${path}/bin/other";
    previewHasNoContext = builtins.getContext "${preview "example" poison}" == { };
    previewCannotBuild = !(builtins.tryEval (preview "example" poison).drvPath).success;
    normalPreservesPackage = normal "example" package == package;
    normalPreservesContext =
      builtins.getContext "${normal "example" package}" == builtins.getContext "${package}";
    normalHasContext = builtins.getContext "${normal "example" package}" != { };
    normalPreservesMainProgram = lib.getExe (normal "example" package) == lib.getExe package;
    normalEmitsNoHashHints = normal.hashAnnotations [ "/metadata/annotations/hash" ] == { };
    previewDeclaresHashHints =
      preview.hashAnnotations [ "/metadata/annotations/hash" ] == {
        "ekn.dev/package-placeholder-hashes" = "[\"/metadata/annotations/hash\"]";
      };
    exposesMode = preview.enabled && !normal.enabled;
    moduleLibraryExportsHelper =
      instance.config.kubernetes.resources.demo.ConfigMap.package-ref.data.path
      == "${(import ../easykubenix/lib/packageRef.nix { }) "example" package}";
  };
in
assert lib.all (result: result) (builtins.attrValues tests);
pkgs.runCommand "easykubenix-check-package-ref" { results = builtins.toJSON tests; } ''
  printf '%s\n' "$results" > "$out"
''
