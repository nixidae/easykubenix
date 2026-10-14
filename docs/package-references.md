# Package references

Use `ekn.lib.packageRef` inside workload package interpolations.
Normal commands receive the original package, including its string context and metadata.
`ekn render --package-placeholders` and `ekn diff --package-placeholders` receive an inspection path without evaluating the package.

```nix
{ ekn, lib, pkgs, ... }:
let
  packageRef = ekn.lib.packageRef;
in
{
  kubernetes.resources.demo.ConfigMap.example.data = {
    command = lib.getExe (packageRef "hello" pkgs.hello);
    dataPath = "${packageRef "hello" pkgs.hello}/share";
  };
}
```

The explicit label supplies the placeholder name and default executable name.
Use `lib.getExe'` when the executable name differs from that label.
Place the helper before interpolation; an already formed string has evaluated its package.
`packageRef.enabled` reports whether the evaluation uses placeholders.
`packageRef.hashAnnotations` declares JSON pointers to derived hashes for preview comparison.
For example, add `packageRef.hashAnnotations [ "/metadata/annotations/configHash" ]` to the object's metadata annotations.
Preview diff ignores package-only changes. Delivery commands retain real package references.
