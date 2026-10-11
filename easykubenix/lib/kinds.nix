# Kind resolution: what a key under `kubernetes.objects.<namespace>` means,
# and which key an object of a given group and kind is filed under. See
# design/kinds.md.
#
# `kubernetes.nix` is the only caller: it chooses the key for a hand-written
# object and files every imported one, so the two agree on what a bare kind
# means.
{ lib }:
let
  # schema.ParseGroupVersion: "v1" is the core group.
  groupOf =
    apiVersion:
    let
      m = builtins.match "([^/]*)/(.*)" apiVersion;
    in
    if m == null then "" else lib.head m;

  # schema.GroupKind.String() and schema.ParseGroupKind, which splits at the
  # first dot.
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

  # The `kubectl get --output=name` form.
  objectName =
    gk: name: "${lib.toLower gk.kind}${lib.optionalString (gk.group != "") ".${gk.group}"}/${name}";

  # version.CompareKubeAwareVersionStrings as a sort key: GA over beta over
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

  # The version discovery reports for a CRD: its highest served version, not
  # its storage version.
  preferredVersion =
    versions:
    lib.head (
      lib.sort (a: b: lib.compareLists lib.compare (versionRank a) (versionRank b) > 0) (
        map (v: v.name) (lib.filter (v: v.served or true) versions)
      )
    );

  # A CustomResourceDefinition as the APIResource discovery reports for it,
  # keyed by its own name, which is the GroupResource string.
  crdResource = crd: {
    name = crd.metadata.name;
    value = {
      name = crd.spec.names.plural;
      singularName = crd.spec.names.singular or (lib.toLower crd.spec.names.kind);
      namespaced = (crd.spec.scope or "Namespaced") == "Namespaced";
      inherit (crd.spec) group;
      version = preferredVersion crd.spec.versions;
      inherit (crd.spec.names) kind;
    };
  };

  # `cfg` is `config.kubernetes`. Reads `apiResources`, `apiMappings` and
  # `kindAliases` only -- never `objects`, which reads this to choose its keys.
  resolver =
    cfg:
    let
      resources = lib.attrValues cfg.apiResources;
      byKind = lib.groupBy (r: r.kind) resources;
      byGK = lib.listToAttrs (map (r: lib.nameValuePair (gkString r) r) resources);
      aliasesByGK = lib.groupBy (alias: gkString (parseGroupKind cfg.kindAliases.${alias})) (
        lib.attrNames cfg.kindAliases
      );

      # What a bare kind means. `apiMappings` first, because the user wrote it;
      # then the only group serving the kind, or the core group, which kubectl
      # puts first; else nothing.
      bareGroup =
        kind:
        if cfg.apiMappings ? ${kind} then
          groupOf cfg.apiMappings.${kind}
        else
          let
            groups = lib.unique (map (r: r.group) (byKind.${kind} or [ ]));
          in
          if lib.length groups == 1 then
            lib.head groups
          else if lib.elem "" groups then
            ""
          else
            null;
    in
    rec {
      # `objects.<namespace>.<key>` to a GroupKind, or null when the key names
      # a kind two non-core groups serve. A bare kind nothing serves resolves
      # to the core group, so an explicit apiVersion still works for it.
      resolveKey =
        key:
        if cfg.kindAliases ? ${key} then
          parseGroupKind cfg.kindAliases.${key}
        else if lib.hasInfix "." key then
          parseGroupKind key
        else
          let
            group = if byKind ? ${key} || cfg.apiMappings ? ${key} then bareGroup key else "";
          in
          if group == null then
            null
          else
            {
              kind = key;
              inherit group;
            };

      apiVersionOf =
        gk:
        let
          mapped = cfg.apiMappings.${gk.kind} or null;
          served = byGK.${gkString gk} or null;
        in
        if mapped != null && groupOf mapped == gk.group then
          mapped
        else if served != null then
          (if served.group == "" then served.version else "${served.group}/${served.version}")
        else
          throw "No apiResources entry or apiMapping for ${gkString gk}";

      # The meta.AmbiguousKindError text.
      ambiguous =
        kind:
        "${kind} matches multiple kinds [${
          lib.concatMapStringsSep " " (r: "${r.group}/${r.version}, Kind=${r.kind}") byKind.${kind}
        }]";

      # The key an object of this GroupKind is filed under: its alias, else
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
        else if (byKind ? ${gk.kind} || cfg.apiMappings ? ${gk.kind}) && bareGroup gk.kind != gk.group then
          gkString gk
        else
          gk.kind;
    };
in
{
  inherit
    crdResource
    gkString
    groupOf
    objectName
    parseGroupKind
    resolver
    ;
}
