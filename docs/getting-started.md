# Getting started

easykubenix writes Kubernetes objects as NixOS-style modules. The function
is `lib.easykubenix`, and the command-line tool is `packages.ekn`.

## Write a deployment

Put this in `deployment.nix`. Pass your own `pkgs`, so that easykubenix uses
your package set:

```nix
{ pkgs ? import <nixpkgs> { } }:
let
  easykubenix = (import (builtins.fetchTarball "https://github.com/nixidae/easykubenix/archive/main.tar.gz") {
    inherit pkgs;
  }).lib.easykubenix;
in
easykubenix {
  modules = [
    {
      kubernetes.objects.default.ConfigMap.app-config.data.mode = "production";
    }
  ];
}
```

`kubernetes.objects` takes the namespace, then the kind, then the name.

## Render and check it

Get `ekn`, then render the objects as YAML:

```console
$ nix shell --file https://github.com/nixidae/easykubenix/archive/main.tar.gz packages.ekn
$ ekn --file deployment.nix render
```

Check each object against the schemas of the pinned Kubernetes version. The
check reaches no cluster and no network:

```console
$ ekn --file deployment.nix schema-check --offline
```

## Apply it

`ekn` applies only to a cluster that the deployment names. Add an environment
to the module, then run the apply:

```nix
ekn.environment = "dev";
```

```console
$ ekn --file deployment.nix kubeapply
```

The first run refuses, and prints the `ekn.clusterUid` line for the cluster
that the kubeconfig selects. Add that line to the module and run the apply
again.

## Next steps

- {doc}`examples` shows Helm charts, list helpers and other modules.
- {doc}`options` lists every option.
