#include "RLViewerGameMode.h"

#include "Components/DirectionalLightComponent.h"
#include "Components/SkyAtmosphereComponent.h"
#include "Components/SkyLightComponent.h"
#include "Engine/DirectionalLight.h"
#include "Engine/Engine.h"
#include "Engine/GameViewportClient.h"
#include "Engine/SkyLight.h"
#include "Engine/World.h"
#include "HAL/FileManager.h"
#include "Kismet/GameplayStatics.h"
#include "Misc/CommandLine.h"
#include "Misc/Paths.h"
#include "RLArena.h"
#include "RLDemoCapture.h"
#include "RLReplayDirector.h"
#include "RLReplayViewer.h"
#include "RLViewerPawn.h"
#include "RLViewerPlayerController.h"
#include "SRLReplayHud.h"

ARLViewerGameMode::ARLViewerGameMode()
{
	DefaultPawnClass = ARLViewerPawn::StaticClass();
	PlayerControllerClass = ARLViewerPlayerController::StaticClass();
}

TArray<FString> ARLViewerGameMode::FindReplayFiles()
{
	const FString Folder = FPaths::ConvertRelativePathToFull(FPaths::ProjectDir() / TEXT("Replays"));
	TArray<FString> Names;
	IFileManager::Get().FindFiles(Names, *(Folder / TEXT("*.rlview.json")), true, false);

	TArray<FString> Paths;
	for (const FString& Name : Names)
	{
		Paths.Add(Folder / Name);
	}
	Paths.Sort([](const FString& A, const FString& B) {
		return IFileManager::Get().GetTimeStamp(*A) > IFileManager::Get().GetTimeStamp(*B);
	});
	return Paths;
}

void ARLViewerGameMode::BeginPlay()
{
	Super::BeginPlay();
	UWorld* World = GetWorld();

	SpawnLighting();
	World->SpawnActor<ARLArena>();
	Director = World->SpawnActor<ARLReplayDirector>();

	if (ARLViewerPawn* Pawn = Cast<ARLViewerPawn>(UGameplayStatics::GetPlayerPawn(this, 0)))
	{
		Pawn->SetDirector(Director);
	}
	Director->OnLiveMatchChanged.AddUObject(this, &ARLViewerGameMode::HandleLiveMatchChanged);

	// -Live (or -Live=7777) listens for `rltracker stream` instead of opening
	// a file; -LiveHost=0.0.0.0 accepts a stream from another computer.
	const TCHAR* CommandLine = FCommandLine::Get();
	FParse::Value(CommandLine, TEXT("LiveHost="), LiveHost);
	if (FParse::Value(CommandLine, TEXT("Live="), LivePort) || FParse::Param(CommandLine, TEXT("Live")))
	{
		StartLive();
		return;
	}

	// A file passed on the command line (-RLReplay="C:/path/match.rlview.json")
	// wins; otherwise open the newest export in the Replays folder. (Not
	// -Replay=, which Unreal takes for its own replay system.)
	FString Path;
	if (!FParse::Value(CommandLine, TEXT("RLReplay="), Path))
	{
		const TArray<FString> Files = FindReplayFiles();
		if (Files.Num() > 0)
		{
			Path = Files[0];
		}
	}

	if (Path.IsEmpty())
	{
		StatusMessage = TEXT("No replays found. Run `rltracker viewer-export` to add matches to the Replays folder.");
		ShowHud();
	}
	else
	{
		OpenReplay(Path);
	}

	// -DemoCapture="/folder" records the README screenshots and GIF frames, then quits.
	FString CaptureDir;
	if (FParse::Value(FCommandLine::Get(), TEXT("DemoCapture="), CaptureDir))
	{
		ARLViewerPawn* Pawn = Cast<ARLViewerPawn>(UGameplayStatics::GetPlayerPawn(this, 0));
		World->SpawnActor<ARLDemoCapture>()->Start(Director, Pawn, CaptureDir);
	}
}

void ARLViewerGameMode::EndPlay(const EEndPlayReason::Type EndPlayReason)
{
	HideHud();
	Super::EndPlay(EndPlayReason);
}

void ARLViewerGameMode::OpenReplay(const FString& Path)
{
	FString Error;
	if (Director && Director->LoadReplay(Path, Error))
	{
		StatusMessage.Reset();
		if (ARLViewerPawn* Pawn = Cast<ARLViewerPawn>(UGameplayStatics::GetPlayerPawn(this, 0)))
		{
			Pawn->SetCameraMode(Director->GetFocusPlayer() != INDEX_NONE ? ERLCameraMode::BallCam : ERLCameraMode::Orbit);
		}
	}
	else
	{
		StatusMessage = FString::Printf(TEXT("Couldn't open %s: %s"), *FPaths::GetCleanFilename(Path), *Error);
	}
	ShowHud();
}

void ARLViewerGameMode::StartLive()
{
	if (!Director)
	{
		return;
	}
	FString Error;
	if (Director->StartLive(LiveHost, LivePort, Error))
	{
		StatusMessage.Reset();
	}
	else
	{
		StatusMessage = FString::Printf(TEXT("Couldn't listen for a live match: %s"), *Error);
	}
	ShowHud();
}

void ARLViewerGameMode::StopLive()
{
	if (Director)
	{
		Director->StopLive();
	}
	ShowHud();
}

void ARLViewerGameMode::HandleLiveMatchChanged(bool bNewMatch)
{
	if (bNewMatch)
	{
		StatusMessage.Reset();
		ARLViewerPawn* Pawn = Cast<ARLViewerPawn>(UGameplayStatics::GetPlayerPawn(this, 0));
		if (Pawn && Director)
		{
			Pawn->SetCameraMode(Director->GetFocusPlayer() != INDEX_NONE ? ERLCameraMode::BallCam : ERLCameraMode::Orbit);
		}
	}
	// The HUD lists the players and moments it was built with, so rebuild it.
	ShowHud();
}

void ARLViewerGameMode::SpawnLighting()
{
	UWorld* World = GetWorld();
	FActorSpawnParameters Params;
	Params.SpawnCollisionHandlingOverride = ESpawnActorCollisionHandlingMethod::AlwaysSpawn;

	// Sun, sky atmosphere and a real-time sky light: Unreal's standard outdoor
	// setup, created in code because the level is empty. Everything is movable
	// because nothing here is baked.
	if (ADirectionalLight* Sun = World->SpawnActor<ADirectionalLight>(FVector(0, 0, 4000), FRotator(-48, 35, 0), Params))
	{
		UDirectionalLightComponent* Light = CastChecked<UDirectionalLightComponent>(Sun->GetLightComponent());
		Light->SetMobility(EComponentMobility::Movable);
		Light->SetIntensity(8.0f);
		Light->SetAtmosphereSunLight(true);
	}
	World->SpawnActor<ASkyAtmosphere>(FVector::ZeroVector, FRotator::ZeroRotator, Params);
	if (ASkyLight* Sky = World->SpawnActor<ASkyLight>(FVector(0, 0, 500), FRotator::ZeroRotator, Params))
	{
		USkyLightComponent* Light = Sky->GetLightComponent();
		Light->SetMobility(EComponentMobility::Movable);
		Light->bRealTimeCapture = true;
		Light->SetIntensity(1.0f);
		Light->RecaptureSky();
	}
}

void ARLViewerGameMode::ShowHud()
{
	HideHud();
	if (!GEngine || !GEngine->GameViewport)
	{
		return;
	}
	ARLViewerPawn* Pawn = Cast<ARLViewerPawn>(UGameplayStatics::GetPlayerPawn(this, 0));
	SAssignNew(Hud, SRLReplayHud).Director(Director).Pawn(Pawn).GameMode(this);
	GEngine->GameViewport->AddViewportWidgetContent(Hud.ToSharedRef(), 10);
}

void ARLViewerGameMode::HideHud()
{
	if (Hud.IsValid() && GEngine && GEngine->GameViewport)
	{
		GEngine->GameViewport->RemoveViewportWidgetContent(Hud.ToSharedRef());
	}
	Hud.Reset();
}
