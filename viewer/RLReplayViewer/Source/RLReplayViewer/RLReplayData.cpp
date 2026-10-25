#include "RLReplayData.h"

#include "Algo/BinarySearch.h"
#include "Dom/JsonObject.h"
#include "Misc/FileHelper.h"
#include "Serialization/JsonReader.h"
#include "Serialization/JsonSerializer.h"

namespace
{
using FJsonArray = TArray<TSharedPtr<FJsonValue>>;

bool ReadNumbers(const TSharedPtr<FJsonObject>& Object, const TCHAR* Field, std::vector<double>& Out)
{
	const FJsonArray* Values = nullptr;
	if (!Object.IsValid() || !Object->TryGetArrayField(Field, Values))
	{
		return false;
	}
	Out.clear();
	Out.reserve(Values->Num());
	for (const TSharedPtr<FJsonValue>& Value : *Values)
	{
		Out.push_back(Value.IsValid() ? Value->AsNumber() : 0.0);
	}
	return true;
}

bool ReadBody(const TSharedPtr<FJsonObject>& Object, FRLBodyTrack& Out)
{
	return ReadNumbers(Object, TEXT("pos"), Out.Pos) && ReadNumbers(Object, TEXT("rot"), Out.Rot);
}

FString StringOr(const TSharedPtr<FJsonObject>& Object, const TCHAR* Field, const FString& Default = FString())
{
	FString Value;
	return Object->TryGetStringField(Field, Value) ? Value : Default;
}

double NumberOr(const TSharedPtr<FJsonObject>& Object, const TCHAR* Field, double Default = 0.0)
{
	double Value = Default;
	return Object->TryGetNumberField(Field, Value) ? Value : Default;
}

bool Fail(FString& OutError, const FString& Message)
{
	OutError = Message;
	return false;
}

TSharedPtr<FJsonObject> ParseObject(const FString& Json)
{
	TSharedPtr<FJsonObject> Root;
	const TSharedRef<TJsonReader<>> Reader = TJsonReaderFactory<>::Create(Json);
	return FJsonSerializer::Deserialize(Reader, Root) ? Root : nullptr;
}

bool ReadPlayers(const TSharedPtr<FJsonObject>& Root, TArray<FRLPlayerInfo>& Out, FString& OutError)
{
	const FJsonArray* Players = nullptr;
	if (!Root->TryGetArrayField(TEXT("players"), Players))
	{
		return Fail(OutError, TEXT("Missing players"));
	}
	for (const TSharedPtr<FJsonValue>& Value : *Players)
	{
		const TSharedPtr<FJsonObject> P = Value.IsValid() ? Value->AsObject() : nullptr;
		if (!P.IsValid())
		{
			return Fail(OutError, TEXT("Bad player entry"));
		}
		FRLPlayerInfo& Info = Out.AddDefaulted_GetRef();
		Info.Id = StringOr(P, TEXT("id"));
		Info.Name = StringOr(P, TEXT("name"), TEXT("Player"));
		Info.Team = static_cast<int32>(NumberOr(P, TEXT("team"))) == 1 ? 1 : 0;
		Info.Car = StringOr(P, TEXT("car"));
	}
	return true;
}

void ReadMatchDetails(const TSharedPtr<FJsonObject>& Root, FRLReplay& R)
{
	R.Source = StringOr(Root, TEXT("source"));
	R.Mode = StringOr(Root, TEXT("mode"));
	R.Playlist = StringOr(Root, TEXT("playlist"));
	R.RecordedBy = StringOr(Root, TEXT("recordedBy"));
	R.Fps = FMath::Clamp(NumberOr(Root, TEXT("fps"), 10.0), 1.0, 120.0);
	const TSharedPtr<FJsonObject>* Score = nullptr;
	if (Root->TryGetObjectField(TEXT("score"), Score))
	{
		R.BlueScore = static_cast<int32>(NumberOr(*Score, TEXT("blue")));
		R.OrangeScore = static_cast<int32>(NumberOr(*Score, TEXT("orange")));
	}
}

FRLGoal ReadGoal(const TSharedPtr<FJsonObject>& G)
{
	FRLGoal Goal;
	Goal.Time = NumberOr(G, TEXT("time"));
	Goal.Team = static_cast<int32>(NumberOr(G, TEXT("team"))) == 1 ? 1 : 0;
	Goal.ScorerId = StringOr(G, TEXT("scorerId"));
	Goal.Clock = StringOr(G, TEXT("clock"));
	const TSharedPtr<FJsonObject>* Score = nullptr;
	if (G->TryGetObjectField(TEXT("score"), Score))
	{
		Goal.ScoreAfter[0] = static_cast<int32>(NumberOr(*Score, TEXT("blue")));
		Goal.ScoreAfter[1] = static_cast<int32>(NumberOr(*Score, TEXT("orange")));
	}
	return Goal;
}

FRLMoment ReadMoment(const TSharedPtr<FJsonObject>& M)
{
	FRLMoment Moment;
	Moment.Time = NumberOr(M, TEXT("time"));
	Moment.Clock = StringOr(M, TEXT("clock"));
	Moment.PlayerId = StringOr(M, TEXT("playerId"));
	Moment.Kind = StringOr(M, TEXT("kind"));
	Moment.Rule = StringOr(M, TEXT("rule"));
	Moment.Title = StringOr(M, TEXT("title"));
	Moment.Severity = StringOr(M, TEXT("severity"), TEXT("low"));
	Moment.Detail = StringOr(M, TEXT("detail"));
	M->TryGetBoolField(TEXT("punished"), Moment.bPunished);
	return Moment;
}
}  // namespace

int32 FRLReplay::FindPlayer(const FString& Id) const
{
	return Players.IndexOfByPredicate([&Id](const FRLPlayerInfo& P) { return P.Id == Id; });
}

double FRLReplay::ClockAt(double Time) const
{
	if (Clock.empty())
	{
		return 0.0;
	}
	const rlcore::SamplePoint S = rlcore::FindSample(Times, Time);
	return Clock[S.Alpha < 0.5 ? S.Index0 : S.Index1];
}

int32 FRLReplay::ScoreAt(double Time, int32 Team) const
{
	const int32 T = Team == 1 ? 1 : 0;
	int32 Score = StartScore[T];
	for (const FRLGoal& Goal : Goals)
	{
		if (Goal.Time > Time)
		{
			break;
		}
		Score = Goal.ScoreAfter[T];
	}
	return Score;
}

void FRLReplay::AddGoal(const FRLGoal& Goal)
{
	const int32 At = Algo::UpperBoundBy(Goals, Goal.Time, &FRLGoal::Time);
	Goals.Insert(Goal, At);
}

void FRLReplay::AddMoment(const FRLMoment& Moment)
{
	const int32 At = Algo::UpperBoundBy(Moments, Moment.Time, &FRLMoment::Time);
	Moments.Insert(Moment, At);
}

std::vector<double> FRLReplay::MomentTimes() const
{
	std::vector<double> Result;
	Result.reserve(Moments.Num());
	for (const FRLMoment& M : Moments)
	{
		Result.push_back(M.Time);
	}
	return Result;
}

bool FRLReplay::LoadFromFile(const FString& Path, FRLReplay& Out, FString& OutError)
{
	FString Json;
	if (!FFileHelper::LoadFileToString(Json, *Path))
	{
		return Fail(OutError, FString::Printf(TEXT("Could not read %s"), *Path));
	}
	return LoadFromString(Json, Out, OutError);
}

bool FRLReplay::LoadFromString(const FString& Json, FRLReplay& Out, FString& OutError)
{
	const TSharedPtr<FJsonObject> Root = ParseObject(Json);
	if (!Root.IsValid())
	{
		return Fail(OutError, TEXT("Not valid JSON"));
	}
	if (StringOr(Root, TEXT("format")) != TEXT("rltracker-viewer"))
	{
		return Fail(OutError, TEXT("Not an rltracker viewer file (run `rltracker viewer-export`)"));
	}
	const int32 Version = static_cast<int32>(NumberOr(Root, TEXT("version")));
	if (Version != SupportedVersion)
	{
		return Fail(OutError, FString::Printf(TEXT("Unsupported viewer format version %d"), Version));
	}

	FRLReplay R;
	ReadMatchDetails(Root, R);
	if (!ReadPlayers(Root, R.Players, OutError))
	{
		return false;
	}

	const TSharedPtr<FJsonObject>* Frames = nullptr;
	if (!Root->TryGetObjectField(TEXT("frames"), Frames))
	{
		return Fail(OutError, TEXT("Missing frames"));
	}
	if (!ReadNumbers(*Frames, TEXT("time"), R.Times) || !ReadNumbers(*Frames, TEXT("clock"), R.Clock))
	{
		return Fail(OutError, TEXT("Missing frame times"));
	}
	const size_t N = R.Times.size();
	if (N == 0 || R.Clock.size() != N || !std::is_sorted(R.Times.begin(), R.Times.end()))
	{
		return Fail(OutError, TEXT("Frame times are empty or inconsistent"));
	}

	std::vector<double> Live;
	ReadNumbers(*Frames, TEXT("live"), Live);
	R.Live.Init(true, static_cast<int32>(N));
	for (size_t I = 0; I < N && I < Live.size(); ++I)
	{
		R.Live[static_cast<int32>(I)] = Live[I] != 0.0;
	}

	const TSharedPtr<FJsonObject>* Ball = nullptr;
	if (!(*Frames)->TryGetObjectField(TEXT("ball"), Ball) || !ReadBody(*Ball, R.Ball))
	{
		return Fail(OutError, TEXT("Missing ball track"));
	}
	if (R.Ball.Pos.size() != 3 * N || R.Ball.Rot.size() != 4 * N)
	{
		return Fail(OutError, TEXT("Ball track length does not match frame count"));
	}

	const FJsonArray* Cars = nullptr;
	if (!(*Frames)->TryGetArrayField(TEXT("cars"), Cars) || Cars->Num() != R.Players.Num())
	{
		return Fail(OutError, TEXT("Car tracks missing or not one per player"));
	}
	for (const TSharedPtr<FJsonValue>& Value : *Cars)
	{
		FRLCarTrack& Track = R.Cars.AddDefaulted_GetRef();
		const TSharedPtr<FJsonObject> C = Value->AsObject();
		if (!ReadBody(C, Track) || !ReadNumbers(C, TEXT("boost"), Track.Boost))
		{
			return Fail(OutError, TEXT("Bad car track"));
		}
		if (Track.Pos.size() != 3 * N || Track.Rot.size() != 4 * N || Track.Boost.size() != N)
		{
			return Fail(OutError, TEXT("Car track length does not match frame count"));
		}
	}

	const FJsonArray* Goals = nullptr;
	if (Root->TryGetArrayField(TEXT("goals"), Goals))
	{
		for (const TSharedPtr<FJsonValue>& Value : *Goals)
		{
			const TSharedPtr<FJsonObject> G = Value.IsValid() ? Value->AsObject() : nullptr;
			if (G.IsValid())
			{
				R.Goals.Add(ReadGoal(G));
			}
		}
		// Files list each goal without a running score; count it up from 0-0.
		R.Goals.StableSort([](const FRLGoal& A, const FRLGoal& B) { return A.Time < B.Time; });
		int32 Running[2] = {0, 0};
		for (FRLGoal& Goal : R.Goals)
		{
			++Running[Goal.Team];
			Goal.ScoreAfter[0] = Running[0];
			Goal.ScoreAfter[1] = Running[1];
		}
	}

	const FJsonArray* Moments = nullptr;
	if (Root->TryGetArrayField(TEXT("moments"), Moments))
	{
		for (const TSharedPtr<FJsonValue>& Value : *Moments)
		{
			const TSharedPtr<FJsonObject> M = Value.IsValid() ? Value->AsObject() : nullptr;
			if (M.IsValid())
			{
				R.Moments.Add(ReadMoment(M));
			}
		}
		R.Moments.StableSort([](const FRLMoment& A, const FRLMoment& B) { return A.Time < B.Time; });
	}

	Out = MoveTemp(R);
	return true;
}

bool FRLReplay::LoadLiveInfo(const FString& Json, FRLReplay& Out, FString& OutError)
{
	const TSharedPtr<FJsonObject> Root = ParseObject(Json);
	if (!Root.IsValid())
	{
		return Fail(OutError, TEXT("Match info isn't valid JSON"));
	}
	FRLReplay R;
	ReadMatchDetails(Root, R);
	if (!ReadPlayers(Root, R.Players, OutError))
	{
		return false;
	}
	if (R.Players.Num() == 0 || R.Players.Num() > 8)
	{
		return Fail(OutError, TEXT("Match info needs 1 to 8 players"));
	}
	R.Cars.SetNum(R.Players.Num());
	R.StartScore[0] = R.BlueScore;
	R.StartScore[1] = R.OrangeScore;
	Out = MoveTemp(R);
	return true;
}

bool FRLLiveEvent::Load(const FString& Json, FRLLiveEvent& Out, FString& OutError)
{
	const TSharedPtr<FJsonObject> Root = ParseObject(Json);
	if (!Root.IsValid())
	{
		return Fail(OutError, TEXT("Event isn't valid JSON"));
	}
	const FString Kind = StringOr(Root, TEXT("event"));
	Out = FRLLiveEvent();
	if (Kind == TEXT("goal"))
	{
		Out.bGoal = true;
		Out.Goal = ReadGoal(Root);
		return true;
	}
	if (Kind == TEXT("moment"))
	{
		Out.Moment = ReadMoment(Root);
		return true;
	}
	return Fail(OutError, FString::Printf(TEXT("Unknown event \"%s\""), *Kind));
}
