/**
  Reference a workload package without evaluating it during package-placeholder previews.
  The label supplies the preview path name and default executable name.
  Normal mode preserves the package and its string context.

  ```nix
  "${ekn.lib.packageRef "program" package}/share/data"
  lib.getExe (ekn.lib.packageRef "program" package)
  ```
*/
{
  enabled ? builtins.getEnv "EKN_PACKAGE_PLACEHOLDERS" == "1",
}:
{
  inherit enabled;
  __functor =
    _: label: package:
    if enabled then
      {
        type = "derivation";
        name = label;
        outPath = "/nix/store/AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA-${label}";
        drvPath = throw "Package placeholders support inspection only.";
        meta.mainProgram = label;
      }
    else
      package;

  /**
    Declare JSON pointers to hashes whose inputs contain package placeholders.
  */
  hashAnnotations =
    paths: if enabled then { "ekn.dev/package-placeholder-hashes" = builtins.toJSON paths; } else { };
}
