{
  description = "Development environment for a Gitit-style static site generator";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
    basilisk-nixpkgs = {
      url = "github:gitit-testing-organization-123/nixpkgs";
      flake = false;
    };
  };

  outputs =
    { nixpkgs, basilisk-nixpkgs, ... }:
    let
      systems = [
        "x86_64-linux"
        "aarch64-linux"
        "x86_64-darwin"
        "aarch64-darwin"
      ];
      forAllSystems = nixpkgs.lib.genAttrs systems;
      qccFor =
        pkgs:
        let
          basiliskPackages = import basilisk-nixpkgs { inherit pkgs; };
        in
        basiliskPackages.basilisk;
      gititpyFor =
        pkgs:
        let
          qcc = qccFor pkgs;
          pythonPackages = pkgs.python312Packages;
          plotPythonPackages = with pythonPackages; [
            matplotlib
            numpy
          ];
          runtimeTools = [
            pkgs.pandoc
            qcc
            pkgs.gnuplot
            pkgs.imagemagick
            pkgs.gnused
          ];
        in
        pythonPackages.buildPythonApplication {
          pname = "gititpy";
          version = "0.1.0";
          pyproject = true;

          src = ./.;

          build-system = with pythonPackages; [
            setuptools
            wheel
          ];

          dependencies = with pythonPackages; [
            jinja2
            pygments
            pylatexenc
          ] ++ plotPythonPackages;

          nativeBuildInputs = [
            pkgs.makeWrapper
            pkgs.stdenv.cc
          ];

          nativeCheckInputs = runtimeTools;

          checkPhase = ''
            runHook preCheck
            echo "building Darcsit helper binaries for tests"
            python -m wiki.darcsit_helpers.build >/dev/null
            echo "running unit tests"
            python -m unittest wiki.tests
            runHook postCheck
          '';

          pythonImportsCheck = [
            "gititpy"
            "wiki"
            "wiki.darcsit_helpers"
          ];

          postFixup = ''
            for program in gititpy gititpy-artifacts; do
              wrapProgram "$out/bin/$program" \
                --prefix PATH : "${pkgs.lib.makeBinPath runtimeTools}"
            done
          '';

          meta = {
            description = "A small Gitit/Darcsit-style static site generator";
            mainProgram = "gititpy";
          };
        };
    in
    {
      packages = forAllSystems (
        system:
        let
          pkgs = nixpkgs.legacyPackages.${system};
          gititpy = gititpyFor pkgs;
        in
        {
          default = gititpy;
          gititpy = gititpy;
        }
      );

      apps = forAllSystems (
        system:
        {
          default = {
            type = "app";
            program = "${nixpkgs.legacyPackages.${system}.lib.getExe (gititpyFor nixpkgs.legacyPackages.${system})}";
          };
        }
      );

      devShells = forAllSystems (
        system:
        let
          pkgs = nixpkgs.legacyPackages.${system};
          qcc = qccFor pkgs;
          python = pkgs.python312.withPackages (
            ps: with ps; [
              jinja2
              matplotlib
              numpy
              pygments
              pylatexenc
              pytest
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
              pkgs.gnused
              pkgs.gnumake
              pkgs.gnuplot
              pkgs.imagemagick
              pkgs.pandoc
              qcc
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
              echo "GititPy dev shell: $(python --version), Pandoc $(pandoc --version | head -n 1 | cut -d' ' -f2)"
            '';
          };
        }
      );
    };
}
