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
        # `e` is outside Nix's base-32 alphabet, so this is never a real path,
        # and a schema's `[0-9a-z]{32}` store-path pattern still accepts it.
        outPath = "/nix/store/eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee-${label}";
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
