{
  description = "Telegram bot to communicate with agents via ACP (Agent Client Protocol)";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
    flake-utils.url = "github:numtide/flake-utils";
    pyproject-nix = {
      url = "github:pyproject-nix/pyproject.nix";
      inputs.nixpkgs.follows = "nixpkgs";
    };
  };

  outputs = { self, nixpkgs, flake-utils, pyproject-nix }:
    flake-utils.lib.eachDefaultSystem (system:
      let
        pkgs = nixpkgs.legacyPackages.${system};
        python = pkgs.python312;
        
        # Load pyproject.toml
        project = pyproject-nix.lib.project.loadPyproject {
          projectRoot = ./.;
        };

        agent-client-protocol = pkgs.python312Packages.buildPythonPackage {
          pname = "agent-client-protocol";
          version = "0.8.1";
          src = pkgs.fetchPypi {
            pname = "agent_client_protocol";
            version = "0.8.1";
            hash = "sha256-G78VZjv1H2SUJZf2OOMqYoTF2pGAVdlnLTUQ6WUUPb0=";
          };
          format = "pyproject";
          nativeBuildInputs = with pkgs.python312Packages; [
            pdm-backend
            setuptools
            wheel
          ];
          propagatedBuildInputs = with pkgs.python312Packages; [
            pydantic
          ];
          doCheck = false;
        };

        # Create a python environment with dependencies
        pythonEnv = pkgs.python312.withPackages (ps: 
          with ps; [
            aiosqlite
            httpx
            python-dotenv
            python-telegram-bot
            platformdirs
            pydantic
            agent-client-protocol
          ]
        );

        # Define the package
        telegram-acp-client = pkgs.python312Packages.buildPythonApplication {
          pname = "telegram-acp-client";
          version = "0.1.0";
          src = ./.;
          format = "pyproject";

          nativeBuildInputs = with pkgs.python312Packages; [
            hatchling
          ];

          propagatedBuildInputs = (with pkgs.python312Packages; [
            aiosqlite
            httpx
            python-dotenv
            python-telegram-bot
            platformdirs
            pydantic
          ]) ++ [ agent-client-protocol ];

          # Disable tests for now as we don't have them configured in Nix yet
          doCheck = false;
        };

      in
      {
        packages.default = telegram-acp-client;

        devShells.default = pkgs.mkShell {
          buildInputs = [
            pkgs.uv
            pythonEnv
          ];
        };
      }
    ) // {
      # NixOS Module
      nixosModules.default = { config, lib, pkgs, ... }:
        with lib;
        let
          cfg = config.services.telegram-acp-client;
          
          # Instance submodule definition
          instanceOpts = { name, ... }: {
            options = {
              enable = mkEnableOption "Telegram ACP Client instance ${name}";
              
              telegramTokenFile = mkOption {
                type = types.path;
                description = "Path to file containing the Telegram Bot Token.";
              };

              allowedUserIds = mkOption {
                type = types.listOf types.int;
                default = [];
                description = "List of Telegram user IDs allowed to use the bot.";
              };

              agentCommand = mkOption {
                type = types.str;
                default = "gemini --experimental-acp";
                description = "The command used to start the agent.";
              };

              logLevel = mkOption {
                type = types.enum [ "DEBUG" "INFO" "WARNING" "ERROR" "CRITICAL" ];
                default = "INFO";
                description = "Logging level for the bot.";
              };

              package = mkOption {
                type = types.package;
                default = self.packages.${pkgs.stdenv.hostPlatform.system}.default;
                description = "The package to use for the bot.";
              };

              stateDir = mkOption {
                type = types.str;
                default = "/var/lib/telegram-acp-client/${name}";
                description = "Directory where the bot will store its database and logs.";
              };

              userProjectsDir = mkOption {
                type = types.str;
                default = "";
                description = "Starting directory for the session path browser.";
              };

              user = mkOption {
                type = types.str;
                default = cfg.user;
                description = "User account under which the service runs.";
              };

              group = mkOption {
                type = types.str;
                default = cfg.group;
                description = "Group under which the service runs.";
              };
            };
          };

        in {
          options.services.telegram-acp-client = {
            enable = mkEnableOption "Telegram ACP Client service";
            user = mkOption {
              type = types.str;
              default = "telegram-acp";
              description = "Default user account under which the service runs.";
            };
            group = mkOption {
              type = types.str;
              default = "telegram-acp";
              description = "Default group under which the service runs.";
            };
            instances = mkOption {
              type = types.attrsOf (types.submodule instanceOpts);
              default = {};
              description = "Configuration for multiple bot instances.";
            };
          };

          config = mkIf cfg.enable {
            systemd.services = mapAttrs' (name: instance: 
              nameValuePair "telegram-acp-client-${name}" {
                description = "Telegram ACP Bot instance - ${name}";
                after = [ "network.target" ];
                wantedBy = [ "multi-user.target" ];
                
                serviceConfig = {
                  Type = "simple";
                  StateDirectory = "telegram-acp-client/${name}";
                  WorkingDirectory = "/var/lib/telegram-acp-client/${name}";
                  # We use a wrapper script to load the token and generate the config
                  ExecStart = pkgs.writeShellScript "telegram-acp-client-${name}-start" ''
                    set -e
                    TOKEN=$(cat ${instance.telegramTokenFile})
                    
                    # Create the JSON config on the fly in the state directory
                    cat > config.json <<EOF
                    {
                      "telegram_token": "$TOKEN",
                      "allowed_user_ids": ${builtins.toJSON instance.allowedUserIds},
                      "agent_command": "${instance.agentCommand}",
                      "user_projects_dir": "${instance.userProjectsDir}",
                      "log_level": "${instance.logLevel}"
                    }
                    EOF
                    
                    export TELEGRAM_ACP_CLIENT_CONFIG_DIR=$(pwd)
                    ${instance.package}/bin/telegram-acp-client run --config config.json
                  '';
                  Restart = "on-failure";
                  User = instance.user;
                  Group = instance.group;
                };
              }
            ) cfg.instances;

            users.users = optionalAttrs (cfg.user == "telegram-acp") {
              telegram-acp = {
                isSystemUser = true;
                group = cfg.group;
                description = "Telegram ACP Client service user";
              };
            };
            users.groups = optionalAttrs (cfg.group == "telegram-acp") {
              telegram-acp = {};
            };
          };
        };
    };
}
