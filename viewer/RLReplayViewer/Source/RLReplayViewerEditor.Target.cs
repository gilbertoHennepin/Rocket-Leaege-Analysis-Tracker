using UnrealBuildTool;

public class RLReplayViewerEditorTarget : TargetRules
{
	public RLReplayViewerEditorTarget(TargetInfo Target) : base(Target)
	{
		Type = TargetType.Editor;
		DefaultBuildSettings = BuildSettingsVersion.Latest;
		IncludeOrderVersion = EngineIncludeOrderVersion.Latest;
		ExtraModuleNames.Add("RLReplayViewer");
	}
}
