# Build

`./ape build` restores NuGet packages, then runs `dotnet build` on the launcher project:

`src/Ape.Launcher/Ape.Launcher.csproj`

That project references `src/Ape.Core/Ape.Core.csproj` and globs every checked-out module:

- `src/Ape.Modules/**/Ape.Module.*.csproj` (libraries)
- `src/Ape.Modules/**/*.Plugin.*.csproj` (plugin assemblies)

MSBuild compiles those references as part of the launcher build and copies their outputs next to the process. A plain `./ape build` therefore compiles the host, Core, and every module and plugin currently checked out under `src/`. It does not walk module folders as separate steps.

The configuration is **Release** unless `APE_BUILD_CONFIGURATION` is set. Output lands in `build/bin/Ape.Launcher/<Configuration>/net10.0/`.

`ApeCore.sln` is for the IDE. `./ape build` uses it only when the launcher project file is missing.

## Names

| Command | What is compiled |
| --- | --- |
| `./ape build` | The launcher project, and through it Core plus every checked-out module and plugin. |
| `./ape build Ape.Launcher` | The same launcher project. `launcher` is the same name. |
| `./ape build Ape.Core` | The same launcher project. `core` is the same name. `Ape.Core` does not build the Core csproj by itself. |
| `./ape build Ape.Module.<Name>` | That module's `<Name>.csproj`, then every `Plugins/**/*.csproj` under it. Afterwards the launcher project is built again so the process output is refreshed. |
| `./ape build plugins` | The launcher project. Its plugin references are what copy plugin DLLs next to the process. |
| `./ape build <PluginShortName>` | That one plugin project (short name or full csproj stem), then the launcher project again. |

Several names in one command combine these steps. `Ape.Launcher` and `Ape.Core` in the same command still produce a single launcher build.

Naming a module does not drop the others from the launcher graph. This command:

```bash
./ape build Ape.Core Ape.Module.DeviceManager Ape.Module.DiscoveryManager
```

first builds the launcher project, which still references every checked-out module, then builds the two named module projects again (library plus their `Plugins/` trees).

A module-only command still ends with that launcher build:

```bash
./ape build Ape.Module.DeviceManager
```

compiles the DeviceManager project and its plugins, then refreshes the launcher output, which compiles Core and the other checked-out modules as launcher references.

`./ape list` prints the module folders and plugin projects the globs can see.
