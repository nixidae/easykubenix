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

## kubenix

[kubenix](https://github.com/hall/kubenix) is what Nix users already write
Kubernetes in. Read at `6a37ce99125b`.

| | kubenix | this design |
| --- | --- | --- |
| Main path | `kubernetes.api.resources.<group>.<version>.<kind>.<name>`, core group spelled `core` | `kubernetes.objects.<namespace>.<key>.<name>` |
| Short path | `kubernetes.resources.<attrName>.<name>` | the same path, with an alias or a bare kind as the key |
| Name you choose | `customTypes.*.attrName`, bound to group, version and kind | `kindAliases.<alias>`, bound to a `GroupKind` string |
| Built-in names | generated camelCase plurals: `deployments`, `configMaps` | the kind: `Deployment`, `ConfigMap` |
| Namespace | `metadata.namespace`, default `kubernetes.namespace` | the path |
| Identity | includes the version | `(group, kind, namespace, name)` |

kubenix's `attrName` is the same idea as `kindAliases`: a name you choose,
bound to a type. This design takes the idea and leaves the version out of the
binding.

Three facts make kubenix modules cheap to take in:

- **kubenix's built-in names are a function of `APIResource`.** Its generator
  (`getAttrName` in `pkgs/generators/k8s/default.nix`) takes the plural
  resource name and the kind: `configmaps` and `ConfigMap` give
  `configMaps`. The prototype computes the same name for each of the 60
  shipped resources that kubenix also generates. kubenix has 14 more names,
  all of them alpha or beta APIs or subresources (`token`, `eviction`) that
  the shipped list does not hold. That gap is in the data, not in the rule.
- **kubenix's list-as-attribute-set form is Kubernetes data.** `containers.nginx`
  becomes a list keyed by `name`, and `volumeMounts."/etc/nginx"` one keyed by
  `mountPath`. The keys come from the OpenAPI extensions
  `x-kubernetes-patch-merge-key` and `x-kubernetes-list-map-keys`.
- **A module can be rewritten before evaluation.**
  solid-kubernetes' `mkComponent` does this with
  `lib.modules.mapDefinitionValue`, and `mkIf`, `mkMerge` and priorities
  survive it.

### kubenix as a submodule

kubenix and easykubenix both declare `kubernetes.resources`, `objects`,
`generated`, `namespace` and `imports`, with different meanings. A submodule
keeps the two apart with no rewriting: `kubenix.instances.<name>` is a
`submoduleWith` whose modules are kubenix's own `k8s` module, so each
instance is a whole kubenix configuration with kubenix's types.

- Its special arguments are what kubenix's `lib/eval-modules.nix` passes:
  `kubenix` (`lib`, `modules`, `evalModules`), `pkgs`, and `lib` extended with
  kubenix's `upstreamables.nix`. One more, `eknConfig`, is the easykubenix
  configuration around it, read-only.
- Each instance's `kubernetes.objects` become easykubenix
  `kubernetes.objects`, under the key an import would choose.
- The option is `visible = "shallow"`, so documentation does not walk
  kubenix's generated tree.
- The kubenix source comes from a `kubenixSrc ? null` argument of
  `default.nix`, passed to modules as a special argument. `default.nix`
  already forwards its special arguments to every nested instance
  (`callerSpecialArgs`). With `null`, the option still exists and evaluates.
  An instance then fails and names the argument:

  ```
  error: `kubenix.instances' is set, but easykubenix was called without
  `kubenixSrc'. Pass the kubenix source:
  ```

  The prototype passes it through the existing `specialArgs` parameter,
  which is what the new argument would do.

`design/kinds/kubenix-submodule/` runs this inside the real easykubenix.
One instance imports kubenix's `deployment` and `custom-resources` examples
unchanged, plus a module that:

- adds a container in list form beside the example's attribute-set
  `containers.nginx`. kubenix's types merge the two.
- reads `config.kubernetes.api.resources.core.v1.ConfigMap.nginx-config.metadata.name`
  and `eknConfig.kubernetes.clusterInfo.ipFamilies`.

A typed patch through `kubenix.instances.web.kubernetes.resources...` and an
untyped one through `kubernetes.objects.web.Deployment.nginx` both apply. The
result is five objects in namespace `web`.

**An unused submodule costs nothing.** With 2000 easykubenix objects and the
option imported but no instance, the evaluator counts 307 more values, 248
more thunks and 132 more function calls than without it, out of 10.2 million
values. Allocation is 471.8 MiB in both. Documentation gains one entry,
`kubenix.instances` itself. An instance in use pays kubenix's own cost per
object; see below.

### Rejected: rewriting kubenix modules into easykubenix

The other route rewrites each kubenix module, the way solid-kubernetes'
`mkComponent` rewrites components, and mounts it at `kubenix.*`:

1. `ekn.lib.fromKubenix module` takes a path, a function or an attribute set,
   as `imports` does, and walks `imports` the same way. Each definition under
   `kubernetes` moves to `kubenix`. Its `mkIf`/`mkMerge` wrappers move with it.
2. A function module gets `kubenix` from the wrapper, not from
   `_module.args`. An `imports` list cannot read `_module.args`. Each
   `kubenix.modules.*` is an empty module, because easykubenix provides that
   behaviour.
3. A function module gets a `config` in which `config.kubernetes` is
   easykubenix's `config.kubenix`. A kubenix module that reads
   `config.kubernetes.resources.configMaps.x.metadata.name` keeps working.
4. easykubenix declares `kubenix.resources.<attrName>.<name>`,
   `kubenix.namespace`, `kubenix.customTypes` and `kubenix.project`, and
   writes each resource to `kubernetes.objects`. The `attrName` resolves
   through `apiResources`, a custom type's own `attrName` first. The
   namespace comes from `metadata.namespace`, then `kubenix.namespace`. The
   key is the one an import would choose. Lists written as attribute sets
   become lists by the OpenAPI merge keys.

`design/kinds/kubenix-wrap.nix` is this wrapper, and
`design/kinds/kubenix-wrapped.nix` runs it on kubenix's `deployment` and
`custom-resources` examples, unchanged:

- Five objects in namespace `web`, under `ConfigMap`, `CronTab`, `Deployment`
  and `Service`.
- An easykubenix `mkForce` through kubenix's own path
  (`kubenix.resources.deployments.nginx...containers.nginx.image`) and one
  through `kubernetes.objects.web.Deployment.nginx.spec.replicas` both apply.
- `containers` and `volumeMounts` come out as lists.
- 0.53 s and 75 MB, against 0.73 s and 132 MB for a separate kubenix
  evaluation (`design/kinds/kubenix.nix`).

```
nix eval --impure --json --expr 'import ./design/kinds/kubenix-wrapped.nix { kubenix = <checkout>; }'
```

Reading `config.kubernetes` through the remapped view works: the module
system hands a module its `config` lazily, so the view is one more lazy
value. What breaks it is the types. kubenix's generated options turn an
attribute set into a list by its merge key, in both directions. Without them:

- One module that writes `containers` as a list and another that writes it as
  an attribute set fail to merge. kubenix modules do this freely, because
  kubenix accepts both.
- A read returns whatever form the author wrote. kubenix always returns a
  list.

Getting both right means kubenix's types, and then the submodule is the
simpler way to have them.

### What kubenix's typed API costs

`design/kinds/typecost/` puts the same N Deployments through kubenix's
generated v1.33 options tree and through easykubenix's `kubeValueType`. Each
Deployment has one container with ports, env, resources and a volume mount,
written as plain lists, which both accept. Medians of three runs, warm store:

| N | kubenix wall | easykubenix wall (`objects`) | kubenix peak RSS | easykubenix peak RSS |
| --- | --- | --- | --- | --- |
| 0 | 0.63 s | 0.58 s | 124 MiB | 141 MiB |
| 100 | 1.01 s | 0.66 s | 282 MiB | 182 MiB |
| 500 | 2.24 s | 1.05 s | 499 MiB | 345 MiB |
| 2000 | 7.77 s | 2.30 s | 1491 MiB | 504 MiB |

Per object, from N = 0 to N = 2000:

| | evaluator CPU | allocated | values |
| --- | --- | --- | --- |
| kubenix | 6.28 ms | 1564 KiB | 32412 |
| easykubenix `objects` | 0.96 ms | 391 KiB | 8566 |
| easykubenix `generated` | 1.17 ms | 394 KiB | 8613 |

- kubenix's fixed cost is small. Its option tree loads lazily, so an empty
  evaluation is 0.63 s against 0.58 s.
- Per object, kubenix costs 3.4 times the wall time, 4 times the allocation
  and 3 times the peak memory at N = 2000. Its CPU time grows faster than
  N: 2.6 ms per object up to N = 100, 6.3 ms up to N = 2000. Nix's
  `cpuTime` (13.0 s) is then above the wall time (7.8 s), so part of it is
  the garbage collector's parallel marking.
- `generated` adds 0.2 ms per object to `objects`: the pipeline and the
  manifest hash.
- What kubenix buys with it is a type error at evaluation, with the option
  path. easykubenix finds the same mistake at `ekn validate`, against the
  real schema.

Only one kind is measured. A kind with a larger schema costs kubenix more.
`python3 design/kinds/typecost/run.py <kubenix checkout> out.json` repeats it,
and `summary.py out.json` prints these tables.

The cost belongs to kubenix's objects only. easykubenix's own objects stay on
`kubeValueType`.

The OpenAPI merge keys are still worth having on easykubenix's side:
`mkNamedList` always keys on `name`, and the same table would key each list
the way Kubernetes declares it.

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
9. **kubenix compatibility as `kubenix.instances.<name>`, a submodule.**
   Recommended, after the kinds work, because it reuses key resolution.
   kubenix's types stay intact, and an unused option costs nothing. The
   source comes from `kubenixSrc ? null` on `default.nix`. Open: whether
   `eknConfig` stays read-only or an instance may also write easykubenix
   options. Recommended: read-only, so the dependency runs one way.
