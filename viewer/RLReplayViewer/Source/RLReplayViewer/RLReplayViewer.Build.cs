using UnrealBuildTool;

public class RLReplayViewer : ModuleRules
{
	public RLReplayViewer(ReadOnlyTargetRules Target) : base(Target)
	{
		PCHUsage = PCHUsageMode.UseExplicitOrSharedPCHs;

		// Lets files in subfolders (Tests/, Core/) include the module's headers by name.
		PublicIncludePaths.Add(ModuleDirectory);

		PublicDependencyModuleNames.AddRange(new string[]
		{
			"Core",
			"CoreUObject",
			"Engine",
			"InputCore",
			"Json",
			"Slate",
			"SlateCore",
			"Sockets",
			"Networking",
		});
	}
}
