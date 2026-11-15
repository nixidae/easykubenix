{
  callPackage,
  nixvenv,
  optionsReference,
}:
callPackage ./shared/sphinx-site.nix { inherit nixvenv; } {
  name = "easykubenix";
  root = ../.;
  pages."options.md" = optionsReference;
}
