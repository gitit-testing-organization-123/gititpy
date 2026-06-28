{
  description = "Development environment for a Django gitit-style wiki";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
  };

  outputs =
    { nixpkgs, ... }:
    let
      systems = [
        "x86_64-linux"
        "aarch64-linux"
        "x86_64-darwin"
        "aarch64-darwin"
      ];
      forAllSystems = nixpkgs.lib.genAttrs systems;
    in
    {
      devShells = forAllSystems (
        system:
        let
          pkgs = nixpkgs.legacyPackages.${system};
          python = pkgs.python312.withPackages (
            ps: with ps; [
              django
              pygments
              pytest
              pytest-django
              setuptools
              wheel
            ]
          );
        in
        {
          default = pkgs.mkShell {
            packages = [
              python
              pkgs.gcc
              pkgs.git
              pkgs.gnumake
              pkgs.pandoc
              pkgs.sqlite
            ];

            env = {
              PYTHONDONTWRITEBYTECODE = "1";
              PYTHONUNBUFFERED = "1";
            };

            shellHook = ''
              if [ ! -x wiki/darcsit_helpers/bin/literate-c ] || [ ! -x wiki/darcsit_helpers/bin/codeblock ] || [ ! -x wiki/darcsit_helpers/bin/pagemagic ]; then
                python -m wiki.darcsit_helpers.build >/dev/null
              fi
              echo "Django dev shell: $(python --version), Django $(python -m django --version), Pandoc $(pandoc --version | head -n 1 | cut -d' ' -f2)"
            '';
          };
        }
      );
    };
}
