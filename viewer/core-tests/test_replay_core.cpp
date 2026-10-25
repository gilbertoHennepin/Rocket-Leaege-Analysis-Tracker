// Unit tests for the engine-free replay viewer core. Plain C++, no engine.

#include "../RLReplayViewer/Source/RLReplayViewer/Core/ReplayCore.h"

#include <cstdio>
#include <functional>
#include <string>
#include <vector>

using namespace rlcore;

static int Failures = 0;
static int Checks = 0;

#define CHECK(Cond)                                                          \
	do                                                                       \
	{                                                                        \
		++Checks;                                                            \
		if (!(Cond))                                                         \
		{                                                                    \
			++Failures;                                                      \
			std::printf("  FAILED %s:%d: %s\n", __FILE__, __LINE__, #Cond); \
		}                                                                    \
	} while (0)

static bool Near(double A, double B, double Tol = 1e-6)
{
	return std::fabs(A - B) <= Tol;
}

static bool Near(const Vec3& A, const Vec3& B, double Tol = 1e-6)
{
	return Near(A.X, B.X, Tol) && Near(A.Y, B.Y, Tol) && Near(A.Z, B.Z, Tol);
}

static Quat AxisAngleZ(double Radians)
{
	return {0.0, 0.0, std::sin(Radians / 2), std::cos(Radians / 2)};
}

static void TestFindSample()
{
	const std::vector<double> Times = {0.0, 0.1, 0.2, 0.3};
	SamplePoint S = FindSample(Times, 0.15);
	CHECK(S.Index0 == 1 && S.Index1 == 2);
	CHECK(Near(S.Alpha, 0.5));

	S = FindSample(Times, 0.1);
	CHECK(S.Index0 == 1 && S.Index1 == 2 && Near(S.Alpha, 0.0));

	S = FindSample(Times, -5.0);
	CHECK(S.Index0 == 0 && S.Index1 == 0 && S.Alpha == 0.0);

	S = FindSample(Times, 99.0);
	CHECK(S.Index0 == 3 && S.Index1 == 3);

	S = FindSample({}, 1.0);
	CHECK(S.Index0 == 0 && S.Index1 == 0);
}

static void TestFlatArrays()
{
	const std::vector<double> Pos = {0, 0, 0, 100, 200, 300};
	CHECK(Near(Vec3At(Pos, 1), {100, 200, 300}));
	CHECK(Near(Vec3At(Pos, 2), {0, 0, 0}));  // out of range -> zero, not a crash

	SamplePoint Half{0, 1, 0.5};
	CHECK(Near(SamplePosition(Pos, Half), {50, 100, 150}));

	const std::vector<double> Rot = {0, 0, 0, 2.0};  // unnormalized identity
	const Quat Q = QuatAt(Rot, 0);
	CHECK(Near(Q.W, 1.0));
	CHECK(Near(QuatAt(Rot, 3).W, 1.0));  // out of range -> identity
}

static void TestRotation()
{
	// Rocket League (and Unreal) use X forward, Y right, Z up. A car with
	// yaw +90 degrees faces +Y.
	const Quat Yaw90 = AxisAngleZ(3.14159265358979 / 2);
	CHECK(Near(Yaw90.Rotate({1, 0, 0}), {0, 1, 0}, 1e-9));
	CHECK(Near(Yaw90.Rotate({0, 0, 1}), {0, 0, 1}, 1e-9));
}

static void TestSlerp()
{
	const Quat A = AxisAngleZ(0.0);
	const Quat B = AxisAngleZ(3.14159265358979 / 2);
	const Quat Mid = Slerp(A, B, 0.5);
	const Vec3 Forward = Mid.Rotate({1, 0, 0});
	CHECK(Near(Forward.X, std::cos(3.14159265358979 / 4), 1e-9));
	CHECK(Near(Forward.Y, std::sin(3.14159265358979 / 4), 1e-9));

	// q and -q are the same rotation: slerp must take the short way round.
	const Quat NegB{-B.X, -B.Y, -B.Z, -B.W};
	const Vec3 ShortWay = Slerp(A, NegB, 0.5).Rotate({1, 0, 0});
	CHECK(Near(ShortWay, Forward, 1e-9));

	// Identical inputs hit the nlerp branch.
	CHECK(Near(Slerp(B, B, 0.3).Rotate({1, 0, 0}), {0, 1, 0}, 1e-9));
	CHECK(Near(Slerp(A, B, 0.0).Rotate({1, 0, 0}), {1, 0, 0}, 1e-9));
	CHECK(Near(Slerp(A, B, 1.0).Rotate({1, 0, 0}), {0, 1, 0}, 1e-9));
}

static void TestFormatClock()
{
	CHECK(FormatClock(300) == "5:00");
	CHECK(FormatClock(187) == "3:07");
	CHECK(FormatClock(9.6) == "0:10");
	CHECK(FormatClock(0) == "OT");
	CHECK(FormatClock(-3) == "OT");
}

static void TestPlaybackClock()
{
	PlaybackClock Clock(10.0, 20.0);
	CHECK(Clock.GetTime() == 10.0);
	CHECK(!Clock.IsPlaying());

	Clock.Tick(1.0);  // paused: no movement
	CHECK(Clock.GetTime() == 10.0);

	Clock.Play();
	Clock.Tick(1.0);
	CHECK(Near(Clock.GetTime(), 11.0));

	Clock.Faster();
	CHECK(Clock.GetSpeed() == 2.0);
	Clock.Tick(1.0);
	CHECK(Near(Clock.GetTime(), 13.0));

	for (int I = 0; I < 10; ++I)
	{
		Clock.Faster();
	}
	CHECK(Clock.GetSpeed() == 4.0);
	for (int I = 0; I < 10; ++I)
	{
		Clock.Slower();
	}
	CHECK(Clock.GetSpeed() == 0.25);

	Clock.Seek(100.0);
	CHECK(Clock.GetTime() == 20.0);
	Clock.Seek(-100.0);
	CHECK(Clock.GetTime() == 10.0);

	Clock.SeekProgress(0.5);
	CHECK(Near(Clock.GetProgress(), 0.5));
	CHECK(Near(Clock.GetTime(), 15.0));

	// Playing past the end stops, and Play() restarts from the beginning.
	Clock.Seek(19.9);
	Clock.Play();
	Clock.Tick(10.0);
	CHECK(Clock.IsAtEnd());
	CHECK(!Clock.IsPlaying());
	Clock.Play();
	CHECK(Clock.GetTime() == 10.0);
	CHECK(Clock.IsPlaying());

	PlaybackClock Empty;
	CHECK(Empty.GetProgress() == 0.0);
}

static void TestMomentNavigation()
{
	const std::vector<double> Moments = {10.0, 20.0, 30.0};
	CHECK(NextMoment(Moments, 0.0) == 0);
	CHECK(NextMoment(Moments, 10.0) == 1);  // sitting on a moment moves to the next one
	CHECK(NextMoment(Moments, 25.0) == 2);
	CHECK(NextMoment(Moments, 30.0) == -1);
	CHECK(PrevMoment(Moments, 35.0) == 2);
	CHECK(PrevMoment(Moments, 20.0) == 0);
	CHECK(PrevMoment(Moments, 10.0) == -1);
	CHECK(NextMoment({}, 1.0) == -1);
	CHECK(PrevMoment({}, 1.0) == -1);
}

static void TestCameras()
{
	const Vec3 Car{0, 0, 17};
	const Vec3 Ball{1000, 0, 93};
	const CameraPose Cam = BallCam(Car, Ball, 400.0, 100.0);
	CHECK(Near(Cam.Location, {-400, 0, 117}));  // behind the car, away from the ball
	CHECK(Near(Cam.LookAt, Ball));

	const CameraPose Under = BallCam(Car, {0, 0, 500}, 400.0, 100.0);
	CHECK(Near((Under.Location - Car).Length(), std::sqrt(400.0 * 400.0 + 100.0 * 100.0), 1e-6));

	const CameraPose Top = Orbit({0, 0, 0}, 0.0, 89.0, 1000.0);
	CHECK(Top.Location.Z > 999.0);
	const CameraPose Clamped = Orbit({0, 0, 0}, 90.0, -40.0, 1000.0);  // pitch clamps to 5 degrees
	CHECK(Clamped.Location.Z > 0.0);
	CHECK(Near(Clamped.Location.X, 0.0, 1e-6));
}

static void TestClampToArena()
{
	CHECK(Near(ClampToArena({100, -200, 500}), {100, -200, 500}));  // inside: untouched
	CHECK(Near(ClampToArena({5000, 0, 300}), {4016, 0, 300}));      // beyond a side wall
	CHECK(Near(ClampToArena({2000, 6000, 300}), {2000, 5040, 300}));  // behind a back wall
	CHECK(Near(ClampToArena({0, 5800, 300}), {0, 5800, 300}));      // inside the goal is fine...
	CHECK(Near(ClampToArena({0, 5800, 700}), {0, 5040, 700}));      // ...but not above the crossbar
	CHECK(Near(ClampToArena({0, 0, 3000}).Z, 1964.0));
	CHECK(Near(ClampToArena({0, 0, -50}).Z, 80.0));

	// Past a corner: pushed back onto the diagonal wall, x and y equally.
	const Vec3 Corner = ClampToArena({3900, 4900, 300});
	CHECK(Near(Corner.X + Corner.Y, 4096 + 5120 - 1152 - 80 * 1.41421356, 1e-6));
	CHECK(Near(3900 - Corner.X, 4900 - Corner.Y, 1e-9));
	CHECK(Near(ClampToArena({-3900, -4900, 300}).X, -Corner.X, 1e-9));
}

int main()
{
	const std::vector<std::pair<const char*, std::function<void()>>> Tests = {
		{"FindSample", TestFindSample},
		{"FlatArrays", TestFlatArrays},
		{"Rotation", TestRotation},
		{"Slerp", TestSlerp},
		{"FormatClock", TestFormatClock},
		{"PlaybackClock", TestPlaybackClock},
		{"MomentNavigation", TestMomentNavigation},
		{"Cameras", TestCameras},
		{"ClampToArena", TestClampToArena},
	};
	for (const auto& [Name, Test] : Tests)
	{
		const int Before = Failures;
		Test();
		std::printf("%s %s\n", Failures == Before ? "ok  " : "FAIL", Name);
	}
	std::printf("%d checks, %d failures\n", Checks, Failures);
	return Failures == 0 ? 0 : 1;
}
