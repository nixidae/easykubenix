# Kinds, aliases and object identity

`kubernetes.objects.<namespace>.<kind>.<name>` uses the Kubernetes kind as an
attribute name. Two operators can serve the same kind in different API groups:
CloudNativePG and the MariaDB operator both serve `Cluster`. Today that fails
in three places.

- Each imported CRD writes `apiMappings.Cluster` with `mkDefault`. Two CRDs
  for `Cluster` give `conflicting definition values`, even with no `Cluster`
  object in the configuration.
- `lib.mkForce` on the mapping removes the error, but the prune scan maps one
  kind to one apiVersion. It never scans the other group.
- easykubenix identifies an object by `(namespace, kind, name)`.
  `generatedByPath`, `importYaml`, `kluctl.nix` and the Python apply keys
  (`apply.py` `_object_key`, `directapply.py` `desired`) all drop the group.

Status: proposal. `design/kinds/` holds a prototype. Nothing in
`easykubenix/` uses it yet.

## What Kubernetes already has

Every shape below comes from Kubernetes. Source paths are relative to
`nix build --file /etc/nixpkgs kubernetes.src`, under
`staging/src/k8s.io/`.

| Concept | Kubernetes form | Source |
| --- | --- | --- |
| A served resource | `APIResource { name, singularName, namespaced, group, version, kind, verbs, shortNames, categories }` | `apimachinery/pkg/apis/meta/v1/types.go` |
| The list of them | `APIResourceList`, the output of `kubectl api-resources --output=json` | same |
| A group and a kind as one string | `GroupKind.String()`: `Cluster.postgresql.cnpg.io`, or `Pod` for the core group. `ParseGroupKind` splits at the first dot. | `apimachinery/pkg/runtime/schema/group_version.go:76,133` |
| A group and a resource as one string | `GroupResource.String()`: `clusters.postgresql.cnpg.io`. A CRD's `metadata.name` must be this. | same, `:69` |
| An object's printed identity | `kubectl get --output=name`: `cluster.postgresql.cnpg.io/db` | `cli-runtime/pkg/printers/name.go:129` |
| A kind two groups serve | `AmbiguousKindError`: `Cluster matches multiple kinds [...]` | `apimachinery/pkg/api/meta/errors.go:60` |
| Which group kubectl picks for a bare kind | The core group first, then the groups in discovery order | `client-go/restmapper/discovery.go:80` |
| A CRD's default version | The highest served version by `CompareKubeAwareVersionStrings`, not the storage version | `apiextensions-apiserver/pkg/apiserver/customresource_discovery_controller.go:249,285` |

Kubernetes has no consumer-side alias for a kind. `shortNames` are aliases
for a resource, and the CRD author chooses them. Kubernetes answers ambiguity
with a qualified name, `Cluster.postgresql.cnpg.io`.

`apiMappingFile` is already an `APIResourceList`.

## Identity

A Kubernetes object is `(group, kind, namespace, name)`. The version is not
part of the identity: the API server converts between versions of one group.
easykubenix uses the same identity, and prints it in the `--output=name`
form.

## Options

### `kubernetes.apiResources."<GroupResource>"`

An `APIResource`, keyed by its `GroupResource` string: `deployments.apps`,
`pods`, `clusters.postgresql.cnpg.io`. For a CRD the key is its
`metadata.name`, so two imports of one CRD meet at one key, and two groups
never meet.

- `apiMappingFile` fills it as `mkOptionDefault`. The file format does not
  change.
- An imported CRD fills it as `mkDefault`, converted the way discovery reports
  it: `name` from `spec.names.plural`, `namespaced` from `spec.scope`, and
  `version` from the preferred served version.
- The user's own definition wins over both.

This replaces `apiMappings`. The `namespaced` field also gives easykubenix
the scope of each kind, which it does not have today.

### `kubernetes.kindAliases.<alias> = "<GroupKind>"`

```nix
kubernetes.kindAliases.PGCluster = "Cluster.postgresql.cnpg.io";
kubernetes.kindAliases.MariaCluster = "Cluster.k8s.mariadb.com";
```

An alias is a name in the attribute path. Its value is a `GroupKind` string,
in the form `ParseGroupKind` reads. The version comes from `apiResources`, so a
chart that moves a CRD to a new version moves the alias with it.

Aliases are optional. Without one, the qualified name works.

## The key under a namespace

`objects.<namespace>.<key>` resolves in this order:

1. A `kindAliases` name gives its `GroupKind`.
2. A key with a dot is a `GroupKind` string: `objects.x."Cluster.k8s.mariadb.com".db`.
3. A bare kind gives the one group that serves it. When several groups serve
   it, the core group wins, as in kubectl. `Event` is served by the core
   group and by `events.k8s.io`, and it means the core group.
4. A bare kind that two non-core groups serve is an error, in the
   `AmbiguousKindError` text:

   ```
   error: Cluster matches multiple kinds [k8s.mariadb.com/v1alpha1, Kind=Cluster postgresql.cnpg.io/v1, Kind=Cluster]
   ```

   kubectl picks by discovery order at this point. easykubenix has no
   discovery order, because `apiResources` is an attribute set. A guess would
   give a wrong group with no message.

The `kind` and `apiVersion` defaults come from the resolved `GroupKind`. An
explicit `apiVersion` still overrides the default. A kind that `apiResources`
does not hold works only with an explicit `apiVersion`, as it does today.

## Imports

`importYaml` and `importHelm` know each object's group and kind before
evaluation. They file it under:

1. its alias, when one is declared (two aliases for one `GroupKind` are an
   error),
2. else the bare kind, when the bare kind resolves to this group,
3. else the `GroupKind` string.

So two charts that both ship `Cluster` import with no declaration: their
objects land at `"Cluster.postgresql.cnpg.io"` and `"Cluster.k8s.mariadb.com"`.

`importYaml` has no `config`, so it takes the `kubernetes` config as a new
argument. `helm.nix` and `importyaml.nix` pass it. `kindAliases` and
`apiResources` must never read `objects`, `resources` or `generated`: the
import reads them to choose its keys. CRDs reach `apiResources` from the
import's own parse.

## Hand-written objects are not moved

`objects.x."Cluster.postgresql.cnpg.io".db` stays at that key. easykubenix does
not move it to the alias. When two keys describe one identity, evaluation
fails and names both:

```
error: Several definitions describe the same Kubernetes object:
  x cluster.postgresql.cnpg.io/db: objects.x."Cluster.postgresql.cnpg.io".db, objects.x.PGCluster.db
```

The same check catches two attribute names with one `metadata.name`, which
easykubenix does not check today.

## Reading

`generatedByPath` and every other key-based output use the import rule
above. Generators and transformers make objects with no key, so they get the
key an import would give them.

Declaring an alias moves imported objects. After `kindAliases.PGCluster`
exists, the CNPG objects are at `objects.x.PGCluster.*`. A module that reads
`objects.x.Cluster.db` gets a missing-attribute error, not a wrong value.

## Migration

- `apiMappings.<Kind> = "<group>/<version>"` stays as a deprecated input. It
  becomes an `apiResources` entry and prints a warning.
- `ekn` reads `apiResources` instead of `apiMappings`. The prune scan gets one
  entry per resource, so it reaches both `Cluster` groups.
- The Python identity keys get the group: `(namespace, group, kind, name)` in
  `_object_key`, the `desired` maps and the prune resolver's `already` set.
  `kluctl.nix` keys its excluded-unit set the same way and changes with them.
  Until that lands, the Nix identity check is the guard.
- `kubernetes.nix` checks Service CIDRs and IP families by the key `Service`.
  Those checks must compare `object.kind` and the group.

## Prototype

`design/kinds/kinds.nix` holds `apiResources`, `kindAliases`, key resolution,
the import keys and the identity check. `objects` keeps its current type.
`cases.nix` loads the shipped `apiResources/v1.33.json`.

`nix eval --impure --json --expr 'import ./design/kinds/cases.nix { case = "<case>"; }'`

- `aliases`: two charts each import a `Cluster` CRD and a `Cluster db`
  object. The objects land at `PGCluster.db` and `MariaCluster.db`. A
  `mkForce` patch through `PGCluster` merges onto the import.
  `PGCluster.other` gets `postgresql.cnpg.io/v1`: the CRD stores `v1beta1`
  and serves `v1`, and discovery prefers `v1`. `Event` gets `v1`.
- `qualified`: no aliases. The imports land at the two `GroupKind` keys, and
  a hand-written `"Cluster.k8s.mariadb.com".db2` joins them.
- `collision`, `renamed`, `ambiguous`: the three errors above.

`design/kinds/bench.nix` measures the identity check on 2000 objects:
1.12–1.18 s with it, 1.14–1.15 s without. The difference is within noise.

## Decisions

1. **`apiResources` holds `APIResource` records keyed by `GroupResource`.**
   Recommended. It is the discovery shape, and `apiMappingFile` already has
   it.
2. **The key under a namespace accepts a `GroupKind` string.** Recommended.
   It is the name kubectl and the API machinery use, and it makes aliases
   optional.
3. **`kindAliases` values are `GroupKind` strings.** Recommended. One string,
   in a form Kubernetes defines, and no submodule.
4. **One alias per `GroupKind`.** Recommended. Two aliases give an import two
   equal answers.
5. **A bare kind: core group first, otherwise an error.** Recommended. It
   matches kubectl where kubectl is deterministic.
6. **Hand-written objects are not moved.** Decided: a collision is an
   evaluation error.
7. **Where the identity check runs.** Recommended: one `throw` over the final
   object list in `kubernetes.nix`, so generators and `kubernetes.crds` are
   covered too. Not an assertion: an assertion cannot read a checked output.
   It looks up the attribute paths only when it fires.
8. **Python identity keys with the group.** A separate change after the Nix
   part.
