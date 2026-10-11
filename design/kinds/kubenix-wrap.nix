# kubenix modules in the easykubenix evaluation. `wrap` rewrites a kubenix
# module -- and every module it imports -- so that its `kubernetes.*`
# definitions land on `kubenix.*`. `module` declares `kubenix.*` and turns
# `kubenix.resources.<attrName>.<name>` into `kubernetes.objects`.
{
  lib,
  kubeValueType,
  prototype,
}:
let
  # kubenix's getAttrName (pkgs/generators/k8s/default.nix): the plural
  # resource name, with the kind's capitals where the letters agree.
  # `configmaps` + `ConfigMap` -> `configMaps`.
  attrNameOf =
    resource: kind:
    let
      kindChars = lib.stringToCharacters kind;
    in
    lib.concatStrings (
      lib.imap0 (
        i: c1:
        let
          c2 = if i >= lib.length kindChars then "" else lib.elemAt kindChars i;
        in
        if lib.hasPrefix "API" kind && i == 0 then
          "A"
        else if i == 0 then
          c1
        else if c2 == "" || lib.toLower c2 != c1 then
          c1
        else
          c2
      ) (lib.stringToCharacters resource)
    );

  # What a kubenix module may import from `kubenix.modules`. easykubenix
  # provides that behaviour itself, so each one is an empty module.
  stub = {
    modules =
      lib.genAttrs
        [
          "k8s"
          "helm"
          "submodules"
          "submodule"
          "docker"
          "testing"
          "istio"
          "base"
        ]
        (name: {
          key = "kubenix-stub:${name}";
        });
  };

  # Top-level keys the module system reads itself, never config.
  structural = [
    "_file"
    "key"
    "disabledModules"
    "imports"
    "options"
    "config"
    "meta"
    "freeformType"
    "_class"
  ];

  # `kubernetes` -> `kubenix`, inside any `mkIf`/`mkMerge`/priority wrapper.
  # kubenix's own `kubenix.project` sits beside it, so the two merge.
  renameConfig = lib.modules.mapDefinitionValue (
    config:
    removeAttrs config [
      "kubernetes"
      "kubenix"
    ]
    // lib.optionalAttrs (config ? kubernetes || config ? kubenix) {
      kubenix = lib.mkMerge (
        lib.optional (config ? kubenix) config.kubenix
        ++ lib.optional (config ? kubernetes) config.kubernetes
      );
    }
  );

  wrapAttrs =
    m:
    let
      imports = map wrap (m.imports or [ ]);
    in
    if m ? config || m ? options then
      m
      // {
        inherit imports;
      }
      // lib.optionalAttrs (m ? config) { config = renameConfig m.config; }
    else
      lib.filterAttrs (n: _: lib.elem n structural) m
      // {
        inherit imports;
        config = renameConfig (removeAttrs m structural);
      };

  # A path, a function or an attribute set, as `imports` allows.
  wrap =
    m:
    if lib.isPath m || lib.isString m then
      {
        _file = toString m;
        key = "kubenix-wrapped:${toString m}";
        imports = [ (wrap (import m)) ];
      }
    else if lib.isFunction m then
      # The module system passes only the formals a function names, and it
      # would look for `kubenix` in `_module.args`, where an import list
      # cannot read it. So `kubenix` comes from here, and `config` is a view
      # in which `config.kubernetes` is easykubenix's `config.kubenix`.
      lib.setFunctionArgs (
        args:
        wrapAttrs (
          m (
            args
            // {
              kubenix = stub;
              config = args.config // {
                kubernetes = args.config.kubenix;
              };
            }
          )
        )
      ) (removeAttrs (lib.functionArgs m) [ "kubenix" ] // { config = false; })
    else
      wrapAttrs m;

  # Stand-in for a table from the OpenAPI document: a list kubenix writes as
  # an attribute set, keyed by `x-kubernetes-patch-merge-key`. The real table
  # is per schema path, not per field name.
  mergeKeys = {
    containers = "name";
    initContainers = "name";
    volumes = "name";
    volumeMounts = "mountPath";
    env = "name";
  };
  toLists =
    value:
    if lib.isList value then
      map toLists value
    else if lib.isAttrs value then
      lib.mapAttrs (
        field: v:
        if mergeKeys ? ${field} && lib.isAttrs v then
          lib.mapAttrsToList (k: e: toLists ({ ${mergeKeys.${field}} = k; } // e)) v
        else
          toLists v
      ) value
    else
      value;

  module =
    { config, ... }:
    let
      cfg = config.kubenix;
      kcfg = config.kubernetes;
      groupOf =
        apiVersion: if lib.hasInfix "/" apiVersion then lib.head (lib.splitString "/" apiVersion) else "";

      # kubenix's attribute names: a custom type's own `attrName`, which its
      # author chose, else the name computed from `apiResources`. A computed
      # name two groups share follows the bare-kind rule: the core group,
      # else none.
      byAttrName = lib.groupBy (r: attrNameOf r.name r.kind) (lib.attrValues kcfg.apiResources);
      customByAttrName = lib.groupBy (t: t.attrName) cfg.customTypes;
      gkOfAttrName =
        attrName:
        let
          rs =
            customByAttrName.${attrName} or byAttrName.${attrName}
              or (throw "kubenix: no served resource has the attribute name `${attrName}'");
          core = lib.filter (r: r.group == "") rs;
          r =
            if lib.length rs == 1 then
              lib.head rs
            else if core != [ ] then
              lib.head core
            else
              throw "kubenix: `${attrName}' names ${
                lib.concatMapStringsSep " and " (r: "${r.kind}.${r.group}") rs
              }";
        in
        {
          inherit (r) group kind version;
        };
    in
    {
      options.kubenix = {
        project = lib.mkOption {
          type = lib.types.str;
          default = "kubenix";
        };
        version = lib.mkOption {
          type = lib.types.str;
          default = "";
        };
        namespace = lib.mkOption {
          type = lib.types.nullOr lib.types.str;
          default = null;
        };
        customTypes = lib.mkOption {
          type = lib.types.listOf (lib.types.attrsOf lib.types.anything);
          default = [ ];
        };
        resources = lib.mkOption {
          type = lib.types.attrsOf (
            lib.types.attrsOf (
              lib.types.submodule (
                { name, ... }:
                {
                  freeformType = kubeValueType;
                  options.metadata = lib.mkOption {
                    type = lib.types.submodule {
                      freeformType = kubeValueType;
                      options.name = lib.mkOption {
                        type = lib.types.str;
                        default = name;
                      };
                    };
                    default = { };
                  };
                }
              )
            )
          );
          default = { };
        };
      };

      config.kubernetes = {
        # kubenix's `customTypes` are served resources.
        apiResources = lib.listToAttrs (
          map (
            t:
            let
              plural = t.name or t.attrName;
            in
            lib.nameValuePair "${plural}.${t.group}" (
              lib.mapAttrs (_: lib.mkDefault) {
                name = plural;
                namespaced = true;
                inherit (t) group version kind;
              }
            )
          ) cfg.customTypes
        );

        objects = lib.mkMerge (
          lib.concatLists (
            lib.mapAttrsToList (
              attrName: objects:
              let
                gk = gkOfAttrName attrName;
              in
              lib.mapAttrsToList (
                name: object:
                let
                  namespace = object.metadata.namespace or cfg.namespace;
                  apiVersion =
                    object.apiVersion or (if gk.group == "" then gk.version else "${gk.group}/${gk.version}");
                in
                {
                  ${if namespace == null then "none" else namespace}.${
                    prototype.keyFor kcfg {
                      inherit (gk) kind;
                      group = groupOf apiVersion;
                    }
                  }.${name} =
                    toLists object
                    // {
                      inherit apiVersion;
                      inherit (gk) kind;
                    }
                    // lib.optionalAttrs (namespace != null) {
                      metadata = object.metadata // {
                        inherit namespace;
                      };
                    };
                }
              ) objects
            ) cfg.resources
          )
        );
      };
    };
in
{
  inherit attrNameOf wrap module;
}
