/**
  Every unit one unit needs, transitively, itself excluded: deepest first,
  each once, keeping the first occurrence. `ekn tofu` takes the order
  literally, one `tofu apply` after another.

  `option` names the attribute set the units live in, for the messages:
  `deployment.units` in a Kubernetes instance, `units` in a root. A cycle
  and an unknown name both throw, naming the path.
*/
{ lib }:
option: units: name:
let
  walk =
    path: current:
    if lib.elem current path then
      throw ''
        `${option}' has a dependency cycle:

          ${lib.concatStringsSep " -> " (path ++ [ current ])}

        A unit cannot depend on itself, directly or through another unit.
      ''
    else
      let
        declared =
          units.${current} or (throw ''
            `${option}.${lib.last path}.dependencies' names unknown unit "${current}".

            Declared units: ${lib.concatStringsSep ", " (lib.attrNames units)}
          '');
      in
      lib.concatMap (walk (path ++ [ current ])) declared.dependencies ++ [ current ];
in
lib.unique (lib.concatMap (walk [ name ]) (units.${name}.dependencies or [ ]))
