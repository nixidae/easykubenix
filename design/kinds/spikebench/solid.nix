# solid-kubernetes' nixlab3 through whichever easykubenix the umbrella hands
# it. Run with SOLID_DEV_CHECKOUTS=$HOME/Code and UMBRELLA_DEV=easykubenix so
# that is this working copy; see solid.py.
#
# Forces what `ekn kubeapply` applies: `kubernetes.generated` and every
# deployment unit's objects.
{
  solid,
  environment ? "nixlab3",
  # "length" for timing; "hash" to compare the two arms' output.
  output ? "length",
}:
let
  kubernetes = (import solid { }).environments.${environment}.config.kubernetes;
  json = builtins.toJSON {
    inherit (kubernetes) generated;
    units = builtins.mapAttrs (_: unit: unit.objects) kubernetes.deploymentUnits;
  };
in
if output == "hash" then builtins.hashString "sha256" json else builtins.stringLength json
