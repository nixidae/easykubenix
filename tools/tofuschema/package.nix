{
  lib,
  buildGoModule,
}:
buildGoModule {
  pname = "ekn-tofuschema";
  version = "0-unstable";
  src = lib.fileset.toSource {
    root = ./.;
    fileset = lib.fileset.unions [
      ./go.mod
      ./go.sum
      ./main.go
    ];
  };
  vendorHash = "sha256-kaokSu+YJ0/n8Dkxuq2eOB/mEMZnycR2KHlCRzx/1dQ=";
  # buildGoModule names the binary after the go.mod `module` directive's last
  # component, not after `pname`.
  meta.mainProgram = "ekn-tofuschema";
}
