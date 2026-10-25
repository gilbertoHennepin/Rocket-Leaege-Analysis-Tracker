// Engine-free core of the replay viewer: frame lookup, interpolation,
// playback clock and camera math.
//
// Nothing here depends on Unreal, so it is unit tested with a plain C++
// compiler in CI (see viewer/core-tests). The Unreal code converts these
// types to FVector / FQuat at the edges.

#pragma once

#include <algorithm>
#include <cmath>
#include <cstddef>
#include <string>
#include <vector>

namespace rlcore
{

struct Vec3
{
	double X = 0.0;
	double Y = 0.0;
	double Z = 0.0;

	Vec3 operator+(const Vec3& O) const { return {X + O.X, Y + O.Y, Z + O.Z}; }
	Vec3 operator-(const Vec3& O) const { return {X - O.X, Y - O.Y, Z - O.Z}; }
	Vec3 operator*(double S) const { return {X * S, Y * S, Z * S}; }
	double Dot(const Vec3& O) const { return X * O.X + Y * O.Y + Z * O.Z; }
	double Length() const { return std::sqrt(Dot(*this)); }

	Vec3 Normalized() const
	{
		const double L = Length();
		return L > 1e-9 ? *this * (1.0 / L) : Vec3{};
	}
};

struct Quat
{
	double X = 0.0;
	double Y = 0.0;
	double Z = 0.0;
	double W = 1.0;

	double Dot(const Quat& O) const { return X * O.X + Y * O.Y + Z * O.Z + W * O.W; }

	Quat Normalized() const
	{
		const double L = std::sqrt(Dot(*this));
		return L > 1e-9 ? Quat{X / L, Y / L, Z / L, W / L} : Quat{};
	}

	// Rotate a vector: v' = q v q^-1 (q must be normalized).
	Vec3 Rotate(const Vec3& V) const
	{
		const Vec3 U{X, Y, Z};
		const Vec3 T = Cross(U, V) * 2.0;
		return V + T * W + Cross(U, T);
	}

	static Vec3 Cross(const Vec3& A, const Vec3& B)
	{
		return {A.Y * B.Z - A.Z * B.Y, A.Z * B.X - A.X * B.Z, A.X * B.Y - A.Y * B.X};
	}
};

inline Vec3 Lerp(const Vec3& A, const Vec3& B, double Alpha)
{
	return A + (B - A) * Alpha;
}

// Spherical interpolation along the shortest path.
inline Quat Slerp(const Quat& A, Quat B, double Alpha)
{
	double CosTheta = A.Dot(B);
	if (CosTheta < 0.0)
	{
		B = {-B.X, -B.Y, -B.Z, -B.W};
		CosTheta = -CosTheta;
	}
	if (CosTheta > 0.9995)
	{
		// Nearly identical: normalized lerp avoids dividing by sin(~0).
		return Quat{A.X + (B.X - A.X) * Alpha, A.Y + (B.Y - A.Y) * Alpha, A.Z + (B.Z - A.Z) * Alpha,
					A.W + (B.W - A.W) * Alpha}
			.Normalized();
	}
	const double Theta = std::acos(CosTheta);
	const double SinTheta = std::sin(Theta);
	const double Wa = std::sin((1.0 - Alpha) * Theta) / SinTheta;
	const double Wb = std::sin(Alpha * Theta) / SinTheta;
	return {A.X * Wa + B.X * Wb, A.Y * Wa + B.Y * Wb, A.Z * Wa + B.Z * Wb, A.W * Wa + B.W * Wb};
}

// Where a time falls between two samples.
struct SamplePoint
{
	std::size_t Index0 = 0;
	std::size_t Index1 = 0;
	double Alpha = 0.0;  // 0 at Index0, 1 at Index1
};

// Find the samples around Time in a sorted list of sample times.
// Times before the first sample or after the last clamp to the ends.
inline SamplePoint FindSample(const std::vector<double>& Times, double Time)
{
	SamplePoint Result;
	if (Times.empty())
	{
		return Result;
	}
	if (Time <= Times.front())
	{
		return Result;
	}
	if (Time >= Times.back())
	{
		Result.Index0 = Result.Index1 = Times.size() - 1;
		return Result;
	}
	const auto Upper = std::upper_bound(Times.begin(), Times.end(), Time);
	Result.Index1 = static_cast<std::size_t>(Upper - Times.begin());
	Result.Index0 = Result.Index1 - 1;
	const double Span = Times[Result.Index1] - Times[Result.Index0];
	Result.Alpha = Span > 0.0 ? (Time - Times[Result.Index0]) / Span : 0.0;
	return Result;
}

// Read element I of a flat [x0, y0, z0, x1, ...] array.
inline Vec3 Vec3At(const std::vector<double>& Flat, std::size_t I)
{
	const std::size_t B = I * 3;
	if (B + 2 >= Flat.size())
	{
		return {};
	}
	return {Flat[B], Flat[B + 1], Flat[B + 2]};
}

inline Quat QuatAt(const std::vector<double>& Flat, std::size_t I)
{
	const std::size_t B = I * 4;
	if (B + 3 >= Flat.size())
	{
		return {};
	}
	return Quat{Flat[B], Flat[B + 1], Flat[B + 2], Flat[B + 3]}.Normalized();
}

inline Vec3 SamplePosition(const std::vector<double>& Flat, const SamplePoint& S)
{
	return Lerp(Vec3At(Flat, S.Index0), Vec3At(Flat, S.Index1), S.Alpha);
}

inline Quat SampleRotation(const std::vector<double>& Flat, const SamplePoint& S)
{
	return Slerp(QuatAt(Flat, S.Index0), QuatAt(Flat, S.Index1), S.Alpha);
}

// "3:07" for 187 seconds left, "OT" once the clock has run out.
inline std::string FormatClock(double SecondsLeft)
{
	if (SecondsLeft <= 0.0)
	{
		return "OT";
	}
	const int Total = static_cast<int>(std::lround(SecondsLeft));
	const int Seconds = Total % 60;
	return std::to_string(Total / 60) + (Seconds < 10 ? ":0" : ":") + std::to_string(Seconds);
}

// Playback state: current time, speed and play/pause, clamped to the replay.
class PlaybackClock
{
public:
	static constexpr double Speeds[] = {0.25, 0.5, 1.0, 2.0, 4.0};
	static constexpr int NumSpeeds = 5;

	PlaybackClock() = default;
	PlaybackClock(double InStart, double InEnd) : Start(InStart), End(std::max(InStart, InEnd)), Time(InStart) {}

	double GetTime() const { return Time; }
	double GetStart() const { return Start; }
	double GetEnd() const { return End; }
	double GetSpeed() const { return Speeds[SpeedIndex]; }
	bool IsPlaying() const { return bPlaying; }
	bool IsAtEnd() const { return Time >= End; }

	// 0..1 position along the timeline, for a slider.
	double GetProgress() const { return End > Start ? (Time - Start) / (End - Start) : 0.0; }

	void Play()
	{
		if (IsAtEnd())
		{
			Time = Start;  // replay from the beginning
		}
		bPlaying = true;
	}
	// Play on from where it is, even at the end: a live match's end keeps moving.
	void Resume() { bPlaying = true; }
	void Pause() { bPlaying = false; }
	void TogglePlay() { bPlaying ? Pause() : Play(); }

	// Change the playable range, for a live match that grows as frames arrive.
	void SetRange(double InStart, double InEnd)
	{
		Start = InStart;
		End = std::max(InStart, InEnd);
		Time = std::clamp(Time, Start, End);
	}

	void Seek(double NewTime) { Time = std::clamp(NewTime, Start, End); }
	void SeekProgress(double Progress) { Seek(Start + std::clamp(Progress, 0.0, 1.0) * (End - Start)); }
	void Step(double Seconds) { Seek(Time + Seconds); }

	void Faster() { SpeedIndex = std::min(SpeedIndex + 1, NumSpeeds - 1); }
	void Slower() { SpeedIndex = std::max(SpeedIndex - 1, 0); }

	// Advance by a real-time delta. Stops at the end.
	void Tick(double DeltaSeconds)
	{
		if (!bPlaying)
		{
			return;
		}
		Seek(Time + DeltaSeconds * GetSpeed());
		if (IsAtEnd())
		{
			bPlaying = false;
		}
	}

private:
	double Start = 0.0;
	double End = 0.0;
	double Time = 0.0;
	int SpeedIndex = 2;  // 1x
	bool bPlaying = false;
};

// Index of the first moment strictly after Time (Times sorted), or -1.
inline int NextMoment(const std::vector<double>& Times, double Time, double Epsilon = 0.05)
{
	const auto It = std::upper_bound(Times.begin(), Times.end(), Time + Epsilon);
	return It == Times.end() ? -1 : static_cast<int>(It - Times.begin());
}

// Index of the last moment strictly before Time (Times sorted), or -1.
inline int PrevMoment(const std::vector<double>& Times, double Time, double Epsilon = 0.05)
{
	const auto It = std::lower_bound(Times.begin(), Times.end(), Time - Epsilon);
	return It == Times.begin() ? -1 : static_cast<int>(It - Times.begin()) - 1;
}

struct CameraPose
{
	Vec3 Location;
	Vec3 LookAt;
};

// Rocket League style "ball cam": behind the car, on the line from the
// ball through the car, raised a little, looking at the ball.
inline CameraPose BallCam(const Vec3& Car, const Vec3& Ball, double Distance = 420.0, double Height = 140.0)
{
	Vec3 Away = Car - Ball;
	Away.Z = 0.0;
	Away = Away.Normalized();
	if (Away.Length() < 0.5)
	{
		Away = {-1.0, 0.0, 0.0};  // car right under the ball: pick any direction
	}
	CameraPose Pose;
	Pose.Location = Car + Away * Distance + Vec3{0.0, 0.0, Height};
	Pose.LookAt = Ball;
	return Pose;
}

// Keep a camera inside the arena, as Rocket League's own camera does, so a
// car near a wall can't put the camera behind it. The bounds are the side and
// back walls (or the goal's back wall, inside a goal mouth), the 45 degree
// corners and the ceiling, less a margin.
inline Vec3 ClampToArena(Vec3 P, double Margin = 80.0)
{
	constexpr double HalfWidth = 4096.0;   // side walls, x
	constexpr double HalfLength = 5120.0;  // back walls, y
	constexpr double CornerCut = 1152.0;
	constexpr double GoalHalfWidth = 892.755;
	constexpr double GoalHeight = 642.775;
	constexpr double GoalDepth = 880.0;
	constexpr double Ceiling = 2044.0;

	const bool bInGoalMouth = std::fabs(P.X) < GoalHalfWidth - Margin && P.Z < GoalHeight - Margin;
	const double MaxX = HalfWidth - Margin;
	const double MaxY = (bInGoalMouth ? HalfLength + GoalDepth : HalfLength) - Margin;
	P.X = std::clamp(P.X, -MaxX, MaxX);
	P.Y = std::clamp(P.Y, -MaxY, MaxY);
	P.Z = std::clamp(P.Z, Margin, Ceiling - Margin);

	// Corners: |x| + |y| may not pass the diagonal wall. Step back along its
	// normal, splitting the excess between x and y.
	const double Diagonal = HalfWidth + HalfLength - CornerCut - Margin * 1.41421356;
	const double Excess = std::fabs(P.X) + std::fabs(P.Y) - Diagonal;
	if (Excess > 0.0)
	{
		P.X -= std::copysign(Excess / 2.0, P.X);
		P.Y -= std::copysign(Excess / 2.0, P.Y);
	}
	return P;
}

// Orbit camera around a target. Yaw and pitch in degrees, pitch above the horizon.
inline CameraPose Orbit(const Vec3& Target, double YawDeg, double PitchDeg, double Distance)
{
	constexpr double DegToRad = 3.14159265358979323846 / 180.0;
	const double Yaw = YawDeg * DegToRad;
	const double Pitch = std::clamp(PitchDeg, 5.0, 89.0) * DegToRad;
	CameraPose Pose;
	Pose.Location = Target + Vec3{std::cos(Pitch) * std::cos(Yaw), std::cos(Pitch) * std::sin(Yaw), std::sin(Pitch)} * Distance;
	Pose.LookAt = Target;
	return Pose;
}

}  // namespace rlcore
