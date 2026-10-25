#pragma once

#include "CoreMinimal.h"
#include "GameFramework/GameModeBase.h"

#include "RLViewerGameMode.generated.h"

class ARLReplayDirector;
class SRLReplayHud;

// Sets up the whole viewer: lighting, arena, replay director, camera and HUD.
UCLASS()
class RLREPLAYVIEWER_API ARLViewerGameMode : public AGameModeBase
{
	GENERATED_BODY()

public:
	ARLViewerGameMode();

	// Exported matches (*.rlview.json) in the project's Replays folder, newest first.
	static TArray<FString> FindReplayFiles();

	// Load a match into the viewer and rebuild the HUD for it.
	void OpenReplay(const FString& Path);

	// Listen for a live match from `rltracker stream` (docs/telemetry.md), or stop.
	void StartLive();
	void StopLive();

	const FString& GetStatusMessage() const { return StatusMessage; }

protected:
	virtual void BeginPlay() override;
	virtual void EndPlay(const EEndPlayReason::Type EndPlayReason) override;

private:
	void SpawnLighting();
	void ShowHud();
	void HideHud();
	void HandleLiveMatchChanged(bool bNewMatch);

	UPROPERTY()
	TObjectPtr<ARLReplayDirector> Director;

	TSharedPtr<SRLReplayHud> Hud;
	FString StatusMessage;
	FString LiveHost = TEXT("127.0.0.1");
	int32 LivePort = 7777;
};
