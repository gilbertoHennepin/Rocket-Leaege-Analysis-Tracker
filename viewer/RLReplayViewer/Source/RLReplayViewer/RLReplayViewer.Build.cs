using UnrealBuildTool;

public class RLReplayViewer : ModuleRules
{
	public RLReplayViewer(ReadOnlyTargetRules Target) : base(Target)
	{
		PCHUsage = PCHUsageMode.UseExplicitOrSharedPCHs;

		PublicDependencyModuleNames.AddRange(new string[]
		{
			"Core",
			"CoreUObject",
			"Engine",
			"InputCore",
			"Json",
			"Slate",
			"SlateCore",
		});
	}
}
