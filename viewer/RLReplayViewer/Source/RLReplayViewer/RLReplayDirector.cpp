#include "RLReplayDirector.h"

#include "Components/SceneComponent.h"
#include "Components/StaticMeshComponent.h"
#include "DrawDebugHelpers.h"
#include "Engine/World.h"
#include "HAL/PlatformTime.h"
#include "Materials/MaterialInstanceDynamic.h"
#include "RLReplayViewer.h"
#include "RLShapes.h"

namespace
{
FVector ToFVector(const rlcore::Vec3& V)
{
	return FVector(V.X, V.Y, V.Z);
}

FQuat ToFQuat(const rlcore::Quat& Q)
{
	// Rocket League replays use Unreal's coordinate system, so the replay
	// quaternion maps straight onto FQuat.
	return FQuat(Q.X, Q.Y, Q.Z, Q.W);
}

// Octane hitbox, the most common car body.
const FVector CarSize(118.0, 84.2, 36.2);
constexpr double BallDiameter = 185.5;

// How long a moment's label stays up around the moment itself.
constexpr double MomentShowBefore = 0.75;
constexpr double MomentShowAfter = 2.5;

// Most datagrams handled in one game frame, so a flood can't stall the game.
constexpr int32 MaxDatagramsPerTick = 4096;

FString FromUtf8(const std::string& Utf8)
{
	const FUTF8ToTCHAR Converted(Utf8.data(), static_cast<int32>(Utf8.size()));
	FString Result;
	Result.AppendChars(Converted.Get(), Converted.Length());
	return Result;
}

void AppendBody(FRLBodyTrack& Track, const rlcore::telemetry::Body& Body)
{
	Track.Pos.insert(Track.Pos.end(), {Body.Pos.X, Body.Pos.Y, Body.Pos.Z});
	Track.Rot.insert(Track.Rot.end(), {Body.Rot.X, Body.Rot.Y, Body.Rot.Z, Body.Rot.W});
}
}  // namespace

ARLReplayDirector::ARLReplayDirector()
{
	PrimaryActorTick.bCanEverTick = true;
	RootComponent = CreateDefaultSubobject<USceneComponent>(TEXT("Root"));
}

bool ARLReplayDirector::LoadReplay(const FString& Path, FString& OutError)
{
	FRLReplay Loaded;
	if (!FRLReplay::LoadFromFile(Path, Loaded, OutError))
	{
		UE_LOG(LogRLViewer, Warning, TEXT("Failed to load %s: %s"), *Path, *OutError);
		return false;
	}

	StopLive();
	ResetMatch();
	bHasLiveInfo = false;
	Replay = MoveTemp(Loaded);
	LoadedPath = Path;
	MomentTimes = Replay.MomentTimes();
	Clock = rlcore::PlaybackClock(Replay.StartTime(), Replay.EndTime());
	FocusPlayer = Replay.FindPlayer(Replay.RecordedBy);
	BuildVisuals();
	UpdateVisuals();
	Clock.Play();

	UE_LOG(LogRLViewer, Log, TEXT("Loaded %s: %s, %d players, %d frames, %d coaching moments"), *Path, *Replay.Mode,
		Replay.Players.Num(), Replay.NumFrames(), Replay.Moments.Num());
	return true;
}

void ARLReplayDirector::ResetMatch()
{
	ClearVisuals();
	Replay = FRLReplay();
	LoadedPath.Reset();
	MomentTimes.clear();
	Clock = rlcore::PlaybackClock();
	Sample = rlcore::SamplePoint();
	FocusPlayer = INDEX_NONE;
	ActiveMoment = INDEX_NONE;
}

void ARLReplayDirector::ClearVisuals()
{
	for (USceneComponent* Component : SpawnedComponents)
	{
		if (Component)
		{
			Component->DestroyComponent();
		}
	}
	SpawnedComponents.Reset();
	Cars.Reset();
	BallMesh = nullptr;
}

void ARLReplayDirector::BuildVisuals()
{
	using namespace RLShapes;

	BallMesh = AddShape(this, Sphere(), FVector::ZeroVector, FVector(BallDiameter), MakeMaterial(this, Colors::Ball));
	SpawnedComponents.Add(BallMesh);

	// Name tags and coaching callouts are drawn by the HUD (SRLWorldLabels),
	// in screen space, so the cars are just shapes.
	for (const FRLPlayerInfo& Player : Replay.Players)
	{
		USceneComponent* Root = NewObject<USceneComponent>(this);
		Root->SetMobility(EComponentMobility::Movable);
		Root->SetupAttachment(RootComponent);
		Root->RegisterComponent();
		AddInstanceComponent(Root);
		SpawnedComponents.Add(Root);
		Cars.Add(Root);

		const FLinearColor TeamColor = Colors::Team(Player.Team);
		UStaticMeshComponent* Body =
			AddShape(this, Cube(), FVector(14, 0, 4), CarSize, MakeMaterial(this, TeamColor), FRotator::ZeroRotator, Root);
		// A darker cone on the nose shows which way the car is facing.
		UStaticMeshComponent* Nose = AddShape(this, Cone(), FVector(14 + CarSize.X / 2 + 12, 0, 4), FVector(40, 40, 30),
			MakeMaterial(this, TeamColor * 0.4f), FRotator(-90, 0, 0), Root);
		SpawnedComponents.Add(Body);
		SpawnedComponents.Add(Nose);
	}
}

void ARLReplayDirector::SetFocusPlayer(int32 PlayerIndex)
{
	FocusPlayer = Replay.Players.IsValidIndex(PlayerIndex) ? PlayerIndex : INDEX_NONE;
}

FVector ARLReplayDirector::GetBallLocation() const
{
	return HasReplay() ? ToFVector(rlcore::SamplePosition(Replay.Ball.Pos, Sample)) : FVector::ZeroVector;
}

FVector ARLReplayDirector::GetCarLocation(int32 PlayerIndex) const
{
	if (!Replay.Cars.IsValidIndex(PlayerIndex))
	{
		return FVector::ZeroVector;
	}
	return ToFVector(rlcore::SamplePosition(Replay.Cars[PlayerIndex].Pos, Sample));
}

double ARLReplayDirector::GetCarBoost(int32 PlayerIndex) const
{
	if (!Replay.Cars.IsValidIndex(PlayerIndex))
	{
		return 0.0;
	}
	const std::vector<double>& Boost = Replay.Cars[PlayerIndex].Boost;
	return Sample.Index0 < Boost.size() ? Boost[Sample.Index0] : 0.0;
}

FString ARLReplayDirector::GetClockText() const
{
	return UTF8_TO_TCHAR(rlcore::FormatClock(Replay.ClockAt(Clock.GetTime())).c_str());
}

void ARLReplayDirector::Tick(float DeltaSeconds)
{
	Super::Tick(DeltaSeconds);
	if (IsLive())
	{
		PumpLive();
	}
	if (IsLive() && bHasLiveInfo)
	{
		TickLive(DeltaSeconds);
	}
	else if (HasReplay())
	{
		Clock.Tick(DeltaSeconds);
	}
	if (!HasReplay())
	{
		return;
	}
	UpdateVisuals();
	DrawBallTrail();
}

void ARLReplayDirector::UpdateVisuals()
{
	Sample = rlcore::FindSample(Replay.Times, Clock.GetTime());
	if (BallMesh)
	{
		BallMesh->SetWorldLocationAndRotation(
			ToFVector(rlcore::SamplePosition(Replay.Ball.Pos, Sample)), ToFQuat(rlcore::SampleRotation(Replay.Ball.Rot, Sample)));
	}
	for (int32 I = 0; I < Cars.Num(); ++I)
	{
		const FRLCarTrack& Track = Replay.Cars[I];
		Cars[I]->SetWorldLocationAndRotation(
			ToFVector(rlcore::SamplePosition(Track.Pos, Sample)), ToFQuat(rlcore::SampleRotation(Track.Rot, Sample)));
	}
	ActiveMoment = FindMomentAt(Clock.GetTime(), INDEX_NONE);
}

void ARLReplayDirector::DrawBallTrail() const
{
#if ENABLE_DRAW_DEBUG
	// The last 1.5 seconds of ball flight, which makes passes and clears easy to read.
	const int32 TrailFrames = FMath::Max(1, FMath::RoundToInt(1.5 * Replay.Fps));
	const int32 End = static_cast<int32>(Sample.Index0);
	const int32 Begin = FMath::Max(0, End - TrailFrames);
	for (int32 I = Begin; I < End; ++I)
	{
		if (!Replay.Live[I] || !Replay.Live[I + 1])
		{
			continue;
		}
		const FVector A = ToFVector(rlcore::Vec3At(Replay.Ball.Pos, I));
		const FVector B = ToFVector(rlcore::Vec3At(Replay.Ball.Pos, I + 1));
		const uint8 Alpha = static_cast<uint8>(255.0 * (I - Begin + 1) / (End - Begin + 1));
		DrawDebugLine(GetWorld(), A, B, FColor(232, 230, 224, Alpha), false, -1.0f, 0, 6.0f);
	}
#endif
}

int32 ARLReplayDirector::FindMomentAt(double Time, int32 PlayerIndex) const
{
	// The most recent moment whose display window contains Time.
	for (int32 I = Replay.Moments.Num() - 1; I >= 0; --I)
	{
		const FRLMoment& M = Replay.Moments[I];
		if (M.Time - MomentShowBefore > Time)
		{
			continue;
		}
		if (M.Time + MomentShowAfter < Time)
		{
			break;  // sorted by time: everything earlier is out of range too
		}
		if (PlayerIndex == INDEX_NONE || Replay.FindPlayer(M.PlayerId) == PlayerIndex)
		{
			return I;
		}
	}
	return INDEX_NONE;
}

void ARLReplayDirector::JumpToMoment(int32 MomentIndex)
{
	if (!Replay.Moments.IsValidIndex(MomentIndex))
	{
		return;
	}
	const FRLMoment& M = Replay.Moments[MomentIndex];
	Clock.Seek(M.Time - MomentLeadIn);
	Clock.Play();
	SetFocusPlayer(Replay.FindPlayer(M.PlayerId));
	UpdateVisuals();
}

void ARLReplayDirector::JumpToNextMoment()
{
	// Measure from the moment we are previewing, not the lead-in time we seeked to.
	const double From = Clock.GetTime() + MomentLeadIn;
	for (int32 I = rlcore::NextMoment(MomentTimes, From); I >= 0 && I < Replay.Moments.Num(); ++I)
	{
		if (FocusPlayer == INDEX_NONE || Replay.FindPlayer(Replay.Moments[I].PlayerId) == FocusPlayer)
		{
			JumpToMoment(I);
			return;
		}
	}
}

void ARLReplayDirector::JumpToPreviousMoment()
{
	const double From = Clock.GetTime() + MomentLeadIn;
	for (int32 I = rlcore::PrevMoment(MomentTimes, From); I >= 0; --I)
	{
		if (FocusPlayer == INDEX_NONE || Replay.FindPlayer(Replay.Moments[I].PlayerId) == FocusPlayer)
		{
			JumpToMoment(I);
			return;
		}
	}
}

void ARLReplayDirector::EndPlay(const EEndPlayReason::Type EndPlayReason)
{
	StopLive();
	Super::EndPlay(EndPlayReason);
}

bool ARLReplayDirector::StartLive(const FString& Host, int32 Port, FString& OutError)
{
	StopLive();
	TUniquePtr<FRLTelemetryReceiver> NewReceiver = MakeUnique<FRLTelemetryReceiver>();
	if (!NewReceiver->Start(Host, Port, OutError))
	{
		UE_LOG(LogRLViewer, Warning, TEXT("Live mode: %s"), *OutError);
		return false;
	}
	Receiver = MoveTemp(NewReceiver);
	Stream = rlcore::telemetry::StreamReceiver();
	bHasLiveInfo = false;
	bFollowLive = true;
	PacketsInWindow = 0;
	PacketsPerSecond = 0.0;
	RateWindowStart = FPlatformTime::Seconds();
	UE_LOG(LogRLViewer, Log, TEXT("Live mode: listening on %s"), *Receiver->GetAddress());
	return true;
}

void ARLReplayDirector::StopLive()
{
	if (Receiver.IsValid())
	{
		Receiver->Stop();
		Receiver.Reset();
		UE_LOG(LogRLViewer, Log, TEXT("Live mode: stopped"));
	}
	// A match that was streaming stays loaded, to scrub through like a file.
	Clock.Pause();
}

void ARLReplayDirector::GoLive()
{
	if (!IsLive() || !bHasLiveInfo || !HasReplay())
	{
		return;
	}
	bFollowLive = true;
	Clock.Seek(LiveEdgeTime);
	Clock.Resume();
	LastShownTime = Clock.GetTime();
}

void ARLReplayDirector::TickLive(float DeltaSeconds)
{
	if (!HasReplay())
	{
		return;
	}

	// The playout buffer picks the moment to show; until it has seen enough
	// packets to know, show the newest frame.
	const rlcore::telemetry::PlayoutBuffer& Playout = Stream.GetPlayout();
	LiveEdgeTime = Replay.EndTime();
	if (Playout.IsReady())
	{
		const double SenderTime = Playout.SenderTimeAt(FPlatformTime::Seconds(), Stream.GetLink().JitterMs);
		LiveEdgeTime = rlcore::telemetry::MatchTimeAt(LiveSentTimes, Replay.Times, SenderTime);
	}
	Clock.SetRange(Replay.StartTime(), Replay.EndTime());

	// Pausing or seeking (the clock moved since last tick without us) leaves
	// the live edge.
	if (bFollowLive && (!Clock.IsPlaying() || Clock.GetTime() != LastShownTime))
	{
		bFollowLive = false;
	}
	if (bFollowLive)
	{
		Clock.Seek(LiveEdgeTime);
	}
	else
	{
		// Behind the edge (paused, scrubbed back, replaying a moment): play at
		// the chosen speed, and rejoin the edge on catching up with it.
		const bool bWasPlaying = Clock.IsPlaying();
		Clock.Tick(DeltaSeconds);
		if (bWasPlaying && Clock.GetTime() >= LiveEdgeTime)
		{
			bFollowLive = true;
			Clock.Seek(LiveEdgeTime);
			Clock.Resume();
		}
	}
	LastShownTime = Clock.GetTime();
}

void ARLReplayDirector::PumpLive()
{
	using rlcore::telemetry::PacketType;
	using rlcore::telemetry::Verdict;

	FRLTelemetryReceiver::FDatagram Datagram;
	rlcore::telemetry::Packet Packet;
	for (int32 Handled = 0; Handled < MaxDatagramsPerTick && Receiver->Dequeue(Datagram); ++Handled)
	{
		++PacketsInWindow;
		const Verdict Result = Stream.Receive(Datagram.Bytes.GetData(), static_cast<std::size_t>(Datagram.Bytes.Num()),
			Datagram.ArrivalSeconds, Packet);
		if (Result != Verdict::Use && Result != Verdict::NewStream)
		{
			continue;
		}
		if (Result == Verdict::NewStream)
		{
			bHasLiveInfo = false;
		}
		switch (Packet.Head.Type)
		{
		case PacketType::Hello:
			if (!bHasLiveInfo)
			{
				ApplyLiveInfo(Packet);
			}
			break;
		case PacketType::Frame:
			if (bHasLiveInfo)
			{
				AppendLiveFrame(Packet);
			}
			break;
		case PacketType::Event:
			if (bHasLiveInfo)
			{
				ApplyLiveEvent(Packet);
			}
			break;
		case PacketType::Bye:
			UE_LOG(LogRLViewer, Log, TEXT("Live mode: the match stream ended"));
			break;
		}
	}

	const double Now = FPlatformTime::Seconds();
	if (Now - RateWindowStart >= 1.0)
	{
		PacketsPerSecond = PacketsInWindow / (Now - RateWindowStart);
		PacketsInWindow = 0;
		RateWindowStart = Now;
	}
}

void ARLReplayDirector::ApplyLiveInfo(const rlcore::telemetry::Packet& Packet)
{
	FRLReplay Info;
	FString Error;
	if (!FRLReplay::LoadLiveInfo(FromUtf8(Packet.Json), Info, Error))
	{
		// Match info is resent every second, so a bad one is only a short wait.
		UE_LOG(LogRLViewer, Warning, TEXT("Live mode: bad match info: %s"), *Error);
		return;
	}
	// A new stream is a new match: drop the old one. The visuals are built on
	// its first frame, when there is something to show.
	ResetMatch();
	Replay = MoveTemp(Info);
	LiveSentTimes.clear();
	FocusPlayer = Replay.FindPlayer(Replay.RecordedBy);
	bHasLiveInfo = true;
	bFollowLive = true;
	bWarnedCarCount = false;
	UE_LOG(LogRLViewer, Log, TEXT("Live mode: following a %s match, %d players"), *Replay.Mode, Replay.Players.Num());
	OnLiveMatchChanged.Broadcast(true);
}

void ARLReplayDirector::AppendLiveFrame(const rlcore::telemetry::Packet& Packet)
{
	const rlcore::telemetry::Frame& Frame = Packet.FrameData;
	if (static_cast<int32>(Frame.Cars.size()) != Replay.Players.Num())
	{
		if (!bWarnedCarCount)
		{
			UE_LOG(LogRLViewer, Warning, TEXT("Live mode: frames have %d cars for %d players; skipping them"),
				static_cast<int32>(Frame.Cars.size()), Replay.Players.Num());
			bWarnedCarCount = true;
		}
		return;
	}

	const bool bFirst = Replay.IsEmpty();
	Replay.Times.push_back(Frame.Time);
	Replay.Clock.push_back(Frame.Clock);
	Replay.Live.Add(Frame.bLive);
	AppendBody(Replay.Ball, Frame.Ball);
	for (int32 I = 0; I < Replay.Cars.Num(); ++I)
	{
		AppendBody(Replay.Cars[I], Frame.Cars[I]);
		Replay.Cars[I].Boost.push_back(Frame.Boost[I]);
	}
	LiveSentTimes.push_back(Packet.Head.SentMs / 1000.0);

	if (bFirst)
	{
		Clock = rlcore::PlaybackClock(Frame.Time, Frame.Time);
		BuildVisuals();
		LiveEdgeTime = Frame.Time;
		GoLive();
		OnLiveMatchChanged.Broadcast(true);
	}
}

void ARLReplayDirector::ApplyLiveEvent(const rlcore::telemetry::Packet& Packet)
{
	FRLLiveEvent Event;
	FString Error;
	if (!FRLLiveEvent::Load(FromUtf8(Packet.Json), Event, Error))
	{
		UE_LOG(LogRLViewer, Warning, TEXT("Live mode: bad event: %s"), *Error);
		return;
	}
	if (Event.bGoal)
	{
		Replay.AddGoal(Event.Goal);
		Replay.BlueScore = Event.Goal.ScoreAfter[0];
		Replay.OrangeScore = Event.Goal.ScoreAfter[1];
		return;
	}
	Replay.AddMoment(Event.Moment);
	MomentTimes = Replay.MomentTimes();
	OnLiveMatchChanged.Broadcast(false);
}

FRLLiveStatus ARLReplayDirector::GetLiveStatus() const
{
	using EState = FRLLiveStatus::EState;
	FRLLiveStatus Status;
	if (!IsLive())
	{
		return Status;
	}
	Status.Address = Receiver->GetAddress();
	Status.PacketsPerSecond = PacketsPerSecond;
	if (!bHasLiveInfo || !HasReplay())
	{
		Status.State = EState::Waiting;
		return Status;
	}
	Status.State = Stream.HasEnded() ? EState::Ended
		: Stream.IsQuiet(FPlatformTime::Seconds()) ? EState::Quiet
		: EState::Live;
	const rlcore::telemetry::LinkStats& Link = Stream.GetLink();
	Status.LossPercent = Link.LossPercent();
	Status.JitterMs = Link.JitterMs;
	Status.BufferMs = 1000.0 * Stream.GetPlayout().Depth(Link.JitterMs);
	Status.BehindSeconds = bFollowLive ? 0.0 : FMath::Max(0.0, LiveEdgeTime - Clock.GetTime());
	Status.bFollowing = bFollowLive;
	return Status;
}
