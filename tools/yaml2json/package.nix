{
  lib,
  buildGoModule,
}:
buildGoModule {
  pname = "ekn-yaml2json";
  version = "0-unstable";
  src = lib.fileset.toSource {
    root = ./.;
    fileset = lib.fileset.unions [
      ./go.mod
      ./go.sum
      ./main.go
      ./main_test.go
    ];
  };
  vendorHash = "sha256-HmRB7qs2rgpP6ovqlqFvIROg/SoNNHfsofj4dPXpjAs=";
  # buildGoModule names the binary after the go.mod `module` directive's last
  # component, not after `pname`.
  meta.mainProgram = "ekn-yaml2json";
}
