using UnrealBuildTool;

public class RLReplayViewerTarget : TargetRules
{
	public RLReplayViewerTarget(TargetInfo Target) : base(Target)
	{
		Type = TargetType.Game;
		DefaultBuildSettings = BuildSettingsVersion.Latest;
		IncludeOrderVersion = EngineIncludeOrderVersion.Latest;
		ExtraModuleNames.Add("RLReplayViewer");
	}
}
