# Prototype of design/kinds.md. `objects` keeps today's plain shape; the key
# under a namespace is a GroupKind string or an alias.
{ lib, kubeValueType }:
let
  # schema.ParseGroupVersion: "v1" is the core group.
  groupOf =
    apiVersion:
    let
      parts = lib.splitString "/" apiVersion;
    in
    if lib.length parts == 2 then lib.head parts else "";

  # schema.GroupKind.String() and schema.ParseGroupKind: split at the first dot.
  gkString = gk: if gk.group == "" then gk.kind else "${gk.kind}.${gk.group}";
  parseGroupKind =
    s:
    let
      m = builtins.match "([^.]*)\\.(.*)" s;
    in
    if m == null then
      {
        kind = s;
        group = "";
      }
    else
      {
        kind = lib.elemAt m 0;
        group = lib.elemAt m 1;
      };

  # The `kubectl get --output=name` form: lower-case kind, then the group.
  objectName =
    gk: name: "${lib.toLower gk.kind}${lib.optionalString (gk.group != "") ".${gk.group}"}/${name}";

  # version.CompareKubeAwareVersionStrings, as a sort key: GA over beta over
  # alpha, higher numbers first, anything else last.
  versionRank =
    v:
    let
      m = builtins.match "v([0-9]+)(alpha|beta)?([0-9]*)" v;
    in
    if m == null then
      [ 0 ]
    else
      [
        1
        (lib.toInt (lib.elemAt m 0))
        (
          {
            alpha = 1;
            beta = 2;
          }
          .${toString (lib.elemAt m 1)} or 3
        )
        (if lib.elemAt m 2 == "" then 0 else lib.toInt (lib.elemAt m 2))
      ];
  preferredVersion =
    versions:
    lib.head (
      lib.sort (a: b: lib.compareLists lib.compare (versionRank a) (versionRank b) > 0) (
        map (v: v.name) (lib.filter (v: v.served or true) versions)
      )
    );

  # A CRD as the APIResource discovery reports for it. `metadata.name` is
  # `<plural>.<group>`, the GroupResource string.
  crdResource = crd: {
    name = crd.metadata.name;
    value = lib.mapAttrs (_: lib.mkDefault) {
      name = crd.spec.names.plural;
      singularName = crd.spec.names.singular or (lib.toLower crd.spec.names.kind);
      namespaced = (crd.spec.scope or "Namespaced") == "Namespaced";
      inherit (crd.spec) group;
      version = preferredVersion crd.spec.versions;
      inherit (crd.spec.names) kind;
    };
  };

  resolver = cfg: rec {
    resources = lib.attrValues cfg.apiResources;
    byKind = lib.groupBy (r: r.kind) resources;
    byGK = lib.listToAttrs (map (r: lib.nameValuePair (gkString r) r) resources);
    aliasesByGK = lib.groupBy (alias: gkString (parseGroupKind cfg.kindAliases.${alias})) (
      lib.attrNames cfg.kindAliases
    );

    # What a bare kind means: the only group serving it, or the core group
    # (kubectl puts /v1 first), or nothing when two other groups serve it.
    bareGroup =
      kind:
      let
        groups = lib.unique (map (r: r.group) (byKind.${kind} or [ ]));
      in
      if lib.length groups == 1 then
        lib.head groups
      else if lib.elem "" groups then
        ""
      else
        null;

    # `objects.<ns>.<key>` to a GroupKind, or null when the key does not
    # decide one.
    resolveKey =
      key:
      if cfg.kindAliases ? ${key} then
        parseGroupKind cfg.kindAliases.${key}
      else if lib.hasInfix "." key then
        parseGroupKind key
      else
        let
          group = bareGroup key;
        in
        if group == null then
          null
        else
          {
            kind = key;
            inherit group;
          };

    # The meta.AmbiguousKindError text.
    ambiguous =
      kind:
      "${kind} matches multiple kinds [${
        lib.concatMapStringsSep " " (r: "${r.group}/${r.version}, Kind=${r.kind}") byKind.${kind}
      }]";

    # The key an object with this GroupKind is filed under: its alias, else
    # the bare kind when that means this group, else the GroupKind string.
    keyFor =
      gk:
      let
        aliases = aliasesByGK.${gkString gk} or [ ];
      in
      if lib.length aliases > 1 then
        throw "kubernetes.kindAliases: ${gkString gk} has several aliases: ${lib.concatStringsSep ", " aliases}"
      else if aliases != [ ] then
        lib.head aliases
      else if bareGroup gk.kind == gk.group then
        gk.kind
      else
        gkString gk;
  };
in
{
  # An APIResourceList (`kubectl api-resources --output=json`) as
  # `apiResources` definitions, keyed by GroupResource string. `group` is
  # omitted for the core group (`omitempty`).
  apiResourceList =
    file:
    lib.listToAttrs (
      map (
        resource:
        lib.nameValuePair (resource.name + lib.optionalString (resource ? group) ".${resource.group}") (
          lib.mapAttrs (_: lib.mkOptionDefault) ({ group = ""; } // resource)
        )
      ) (lib.importJSON file).resources
    );

  # Stand-in for `importYaml`. CRDs feed `apiResources`; every other object is
  # filed under `keyFor`. `cfg` is the same fixpoint the CRDs feed, and nothing
  # here reads `objects`.
  importObjects =
    cfg: stream:
    let
      isCRD = object: object.kind == "CustomResourceDefinition";
      r = resolver cfg;
    in
    {
      apiResources = lib.listToAttrs (map crdResource (lib.filter isCRD stream));
      objects = lib.mkMerge (
        map (
          object:
          let
            key = r.keyFor {
              inherit (object) kind;
              group = groupOf object.apiVersion;
            };
          in
          {
            ${object.metadata.namespace}.${key}.${object.metadata.name} = object;
          }
        ) (lib.filter (object: !isCRD object) stream)
      );
    };

  module =
    { config, ... }:
    let
      cfg = config.kubernetes;
      r = resolver cfg;
      objectType =
        key:
        let
          gk = r.resolveKey key;
          served = r.byGK.${gkString gk} or null;
        in
        lib.types.submodule (
          { name, ... }:
          {
            freeformType = kubeValueType;
            options.kind = lib.mkOption {
              type = lib.types.str;
              default = if gk == null then key else gk.kind;
            };
            options.apiVersion = lib.mkOption {
              type = lib.types.str;
              default =
                if gk == null then
                  throw (r.ambiguous key)
                else if served == null then
                  throw "No served resource for ${gkString gk}; set apiVersion."
                else if served.group == "" then
                  served.version
                else
                  "${served.group}/${served.version}";
            };
            options.metadata.name = lib.mkOption {
              type = lib.types.str;
              default = name;
            };
          }
        );

      flat = lib.concatLists (
        lib.mapAttrsToList (
          namespace: keys:
          lib.concatLists (
            lib.mapAttrsToList (
              key: objects:
              lib.mapAttrsToList (
                name: object:
                let
                  gk = {
                    inherit (object) kind;
                    group = groupOf object.apiVersion;
                  };
                in
                {
                  path = lib.showAttrPath [
                    "objects"
                    namespace
                    key
                    name
                  ];
                  identity = "${namespace} ${objectName gk object.metadata.name}";
                  inherit object;
                }
              ) objects
            ) keys
          )
        ) cfg.objects
      );
      collisions = lib.filterAttrs (_: es: lib.length es > 1) (lib.groupBy (e: e.identity) flat);
    in
    {
      options.kubernetes = {
        # Keyed by GroupResource string, which is a CRD's `metadata.name`.
        apiResources = lib.mkOption {
          type = lib.types.attrsOf (
            lib.types.submodule {
              freeformType = lib.types.attrsOf lib.types.anything;
              options = {
                name = lib.mkOption { type = lib.types.str; };
                namespaced = lib.mkOption { type = lib.types.bool; };
                group = lib.mkOption { type = lib.types.str; };
                version = lib.mkOption { type = lib.types.str; };
                kind = lib.mkOption { type = lib.types.str; };
              };
            }
          );
          default = { };
        };
        kindAliases = lib.mkOption {
          type = lib.types.attrsOf lib.types.str;
          default = { };
        };
        objects = lib.mkOption {
          type = lib.types.attrsOf (
            lib.types.attrsOf (
              lib.types.submodule (
                { name, ... }:
                {
                  freeformType = lib.types.attrsOf (objectType name);
                }
              )
            )
          );
          default = { };
        };
        generated = lib.mkOption {
          type = lib.types.anything;
          readOnly = true;
        };
      };
      config.kubernetes.generated = lib.throwIf (collisions != { }) ''
        Several definitions describe the same Kubernetes object:
        ${lib.concatStringsSep "\n" (
          lib.mapAttrsToList (
            identity: es: "  ${identity}: ${lib.concatMapStringsSep ", " (e: e.path) es}"
          ) collisions
        )}
      '' (map (e: e.object) flat);
    };
}
