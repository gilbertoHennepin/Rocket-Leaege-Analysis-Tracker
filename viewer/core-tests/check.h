// A small test harness for the engine-free viewer core: CHECK records a
// failure and carries on, RunTests prints a line per test and a summary.

#pragma once

#include "../RLReplayViewer/Source/RLReplayViewer/Core/ReplayCore.h"

#include <cmath>
#include <cstdio>
#include <functional>
#include <utility>
#include <vector>

inline int Failures = 0;
inline int Checks = 0;

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

inline bool Near(double A, double B, double Tol = 1e-6)
{
	return std::fabs(A - B) <= Tol;
}

inline bool Near(const rlcore::Vec3& A, const rlcore::Vec3& B, double Tol = 1e-6)
{
	return Near(A.X, B.X, Tol) && Near(A.Y, B.Y, Tol) && Near(A.Z, B.Z, Tol);
}

using TestList = std::vector<std::pair<const char*, std::function<void()>>>;

inline int RunTests(const TestList& Tests)
{
	for (const auto& [Name, Test] : Tests)
	{
		const int Before = Failures;
		Test();
		std::printf("%s %s\n", Failures == Before ? "ok  " : "FAIL", Name);
	}
	std::printf("%d checks, %d failures\n", Checks, Failures);
	return Failures == 0 ? 0 : 1;
}
