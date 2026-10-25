// Engine-free side of live mode: the RLT1 wire format sent by
// `rltracker stream`, the receiver rules, link statistics, and the playout
// buffer that turns packets arriving at uneven times into smooth motion.
//
// The protocol is documented in docs/telemetry.md, and the Python listener
// (src/rltracker/telemetry.py) follows the same rules. Like ReplayCore.h,
// nothing here depends on Unreal, so it is unit tested with a plain C++
// compiler in CI (see viewer/core-tests).

#pragma once

#include "ReplayCore.h"

#include <algorithm>
#include <cmath>
#include <cstddef>
#include <cstdint>
#include <cstring>
#include <string>
#include <unordered_set>
#include <vector>

namespace rlcore::telemetry
{

constexpr std::uint8_t ProtocolVersion = 1;
constexpr int DefaultPort = 7777;
constexpr std::size_t HeaderSize = 16;
constexpr std::size_t FrameHeadSize = 10;  // match time, game clock, live flag, car count
constexpr std::size_t BodySize = 20;       // position (3 x float32) and rotation (4 x int16)
constexpr std::size_t CarSize = BodySize + 1;  // and boost (uint8)
constexpr std::size_t EventIdSize = 4;
constexpr std::uint32_t DuplicateWindow = 1024;  // how many recent sequence numbers are remembered
constexpr double StreamTimeout = 2.0;  // seconds of silence before another stream may take over

enum class PacketType : std::uint8_t
{
	Hello = 1,  // match info (JSON), resent every second
	Frame = 2,  // ball and car state (binary)
	Event = 3,  // a goal or coaching moment (event id, then JSON), sent three times
	Bye = 4,    // the stream is over (sent three times)
};

struct Header
{
	PacketType Type = PacketType::Hello;
	std::uint16_t Stream = 0;    // random id, new for every match streamed
	std::uint32_t Sequence = 0;  // counts every packet of a stream, from 0
	std::uint32_t SentMs = 0;    // sender's clock: ms since the stream started
};

struct Body
{
	Vec3 Pos;
	Quat Rot;
};

struct Frame
{
	double Time = 0.0;   // match time, seconds
	double Clock = 0.0;  // game clock, seconds left
	bool bLive = false;  // ball in play
	Body Ball;
	std::vector<Body> Cars;  // in player order
	std::vector<int> Boost;  // 0-100, same order as Cars
};

struct Packet
{
	Header Head;
	Frame FrameData;            // frame packets
	std::uint32_t EventId = 0;  // event packets
	std::string Json;           // hello and event payloads, UTF-8
};

namespace detail
{
// Little-endian reads, whatever the machine's byte order. Callers check
// the remaining length before reading.
class ByteReader
{
public:
	ByteReader(const std::uint8_t* InData, std::size_t InSize) : Data(InData), Size(InSize) {}

	std::size_t Remaining() const { return Size - Offset; }
	const std::uint8_t* Here() const { return Data + Offset; }

	std::uint8_t U8() { return Data[Offset++]; }

	std::uint16_t U16()
	{
		const std::uint16_t Value = static_cast<std::uint16_t>(Data[Offset] | (Data[Offset + 1] << 8));
		Offset += 2;
		return Value;
	}

	std::uint32_t U32()
	{
		std::uint32_t Value = 0;
		for (std::size_t I = 4; I-- > 0;)
		{
			Value = (Value << 8) | static_cast<std::uint32_t>(Data[Offset + I]);
		}
		Offset += 4;
		return Value;
	}

	float F32()
	{
		const std::uint32_t Bits = U32();
		float Value = 0.0f;
		std::memcpy(&Value, &Bits, sizeof Value);
		return Value;
	}

	// Quaternion components travel as int16 scaled by 32767.
	double Unit16() { return static_cast<std::int16_t>(U16()) / 32767.0; }

	Body ReadBody()
	{
		Body Result;
		Result.Pos.X = F32();
		Result.Pos.Y = F32();
		Result.Pos.Z = F32();
		Result.Rot.X = Unit16();
		Result.Rot.Y = Unit16();
		Result.Rot.Z = Unit16();
		Result.Rot.W = Unit16();
		Result.Rot = Result.Rot.Normalized();
		return Result;
	}

private:
	const std::uint8_t* Data;
	std::size_t Size;
	std::size_t Offset = 0;
};

inline bool Fail(std::string& OutError, const char* Message)
{
	OutError = Message;
	return false;
}

inline bool IsFinite(const Vec3& V)
{
	return std::isfinite(V.X) && std::isfinite(V.Y) && std::isfinite(V.Z);
}

inline bool ParseFrame(ByteReader& Reader, Frame& Out, std::string& OutError)
{
	if (Reader.Remaining() < FrameHeadSize + BodySize)
	{
		return Fail(OutError, "truncated frame");
	}
	Out.Time = Reader.F32();
	Out.Clock = Reader.F32();
	Out.bLive = Reader.U8() != 0;
	const std::size_t NumCars = Reader.U8();
	if (Reader.Remaining() != BodySize + NumCars * CarSize)
	{
		return Fail(OutError, "frame length doesn't match its car count");
	}
	Out.Ball = Reader.ReadBody();
	bool bFinite = std::isfinite(Out.Time) && std::isfinite(Out.Clock) && IsFinite(Out.Ball.Pos);
	for (std::size_t I = 0; I < NumCars; ++I)
	{
		Out.Cars.push_back(Reader.ReadBody());
		Out.Boost.push_back(std::min(static_cast<int>(Reader.U8()), 100));
		bFinite = bFinite && IsFinite(Out.Cars.back().Pos);
	}
	if (!bFinite)
	{
		return Fail(OutError, "frame has a number that isn't finite");
	}
	return true;
}
}  // namespace detail

// Parse one datagram. The socket is open to the network, so anything that
// isn't a well-formed packet is rejected with a reason, and every length is
// checked before it is used.
inline bool ParsePacket(const std::uint8_t* Data, std::size_t Size, Packet& Out, std::string& OutError)
{
	Out = Packet{};
	if (Data == nullptr || Size < HeaderSize)
	{
		return detail::Fail(OutError, "shorter than the header");
	}
	if (std::memcmp(Data, "RLT1", 4) != 0)
	{
		return detail::Fail(OutError, "not an rltracker telemetry packet");
	}
	detail::ByteReader Reader(Data + 4, Size - 4);
	if (Reader.U8() != ProtocolVersion)
	{
		return detail::Fail(OutError, "unsupported protocol version");
	}
	const std::uint8_t Type = Reader.U8();
	if (Type < static_cast<std::uint8_t>(PacketType::Hello) || Type > static_cast<std::uint8_t>(PacketType::Bye))
	{
		return detail::Fail(OutError, "unknown packet type");
	}
	Out.Head.Type = static_cast<PacketType>(Type);
	Out.Head.Stream = Reader.U16();
	Out.Head.Sequence = Reader.U32();
	Out.Head.SentMs = Reader.U32();

	if (Out.Head.Type == PacketType::Frame)
	{
		return detail::ParseFrame(Reader, Out.FrameData, OutError);
	}
	if (Out.Head.Type == PacketType::Event)
	{
		if (Reader.Remaining() < EventIdSize)
		{
			return detail::Fail(OutError, "event without an id");
		}
		Out.EventId = Reader.U32();
	}
	if (Out.Head.Type != PacketType::Bye)
	{
		Out.Json.assign(reinterpret_cast<const char*>(Reader.Here()), Reader.Remaining());
	}
	return true;
}

// Loss, reordering, duplicates and jitter, from sequence numbers and send
// times. The same algorithm as StreamStats in the Python listener.
class LinkStats
{
public:
	// Record a packet. Returns false for a duplicate, or one too old to tell,
	// which the caller should ignore.
	bool Add(std::uint32_t Sequence, std::uint32_t SentMs, double ArrivalSeconds)
	{
		if (!bStarted)
		{
			bStarted = true;
			First = Highest = Sequence;
		}
		else if (Sequence > Highest)
		{
			// The slots of the numbers skipped over now stand for newer packets.
			const std::uint32_t Ahead = std::min(Sequence - Highest, DuplicateWindow);
			for (std::uint32_t I = 1; I <= Ahead; ++I)
			{
				Seen[(Highest + I) % DuplicateWindow] = false;
			}
			Highest = Sequence;
		}
		else if (Highest - Sequence >= DuplicateWindow)
		{
			++Late;
			return false;
		}
		else if (Seen[Sequence % DuplicateWindow])
		{
			++Duplicates;
			return false;
		}
		else
		{
			++Reordered;
			First = std::min(First, Sequence);
		}
		Seen[Sequence % DuplicateWindow] = true;
		++Received;

		// RFC 3550 interarrival jitter: a running average of how much the
		// spacing between arrivals differs from the spacing between sends.
		const double Transit = ArrivalSeconds * 1000.0 - SentMs;
		if (bHasTransit)
		{
			JitterMs += (std::fabs(Transit - LastTransit) - JitterMs) / 16.0;
		}
		LastTransit = Transit;
		bHasTransit = true;
		return true;
	}

	std::uint64_t Expected() const { return bStarted ? static_cast<std::uint64_t>(Highest) - First + 1 : 0; }
	std::uint64_t Lost() const { return Expected() > Received ? Expected() - Received : 0; }

	double LossPercent() const
	{
		return Expected() > 0 ? 100.0 * static_cast<double>(Lost()) / static_cast<double>(Expected()) : 0.0;
	}

	std::uint64_t Received = 0;
	std::uint64_t Duplicates = 0;
	std::uint64_t Reordered = 0;
	std::uint64_t Late = 0;  // older than the window: too late to tell, or to use
	double JitterMs = 0.0;

private:
	bool bStarted = false;
	std::uint32_t First = 0;
	std::uint32_t Highest = 0;
	std::vector<bool> Seen = std::vector<bool>(DuplicateWindow, false);
	bool bHasTransit = false;
	double LastTransit = 0.0;
};

// Decides which moment of a live stream to show. Packets carry the sender's
// clock, so the viewer shows "now on the sender's clock, minus a delay":
//   the fastest transit seen lately (the clock offset plus the network's
//     base delay; a windowed minimum, so it follows slow drift),
//   + one frame interval (to interpolate, the frame after the shown time
//     must already be here),
//   + four times the RFC 3550 jitter (headroom for packets that run late).
// A clean link plays almost at once; a jittery one buffers a little deeper.
class PlayoutBuffer
{
public:
	static constexpr double MinTransitWindow = 10.0;  // seconds
	static constexpr double JitterMargin = 4.0;

	void AddPacket(std::uint32_t SentMs, double ArrivalSeconds)
	{
		const double Transit = ArrivalSeconds - SentMs / 1000.0;
		if (!bHasTransit || ArrivalSeconds - WindowStart > MinTransitWindow)
		{
			// Start a new window, keeping the last one's minimum as well, so the
			// minimum always covers between one and two windows.
			PreviousMin = bHasTransit ? CurrentMin : Transit;
			CurrentMin = Transit;
			WindowStart = ArrivalSeconds;
			bHasTransit = true;
		}
		CurrentMin = std::min(CurrentMin, Transit);
	}

	// Frames must be added in order (stale ones are dropped before this).
	void AddFrame(std::uint32_t SentMs)
	{
		const double Sent = SentMs / 1000.0;
		if (bHasFrame && Sent > LastFrameSent)
		{
			const double Gap = Sent - LastFrameSent;
			FrameInterval = FrameInterval > 0.0 ? FrameInterval + (Gap - FrameInterval) / 8.0 : Gap;
		}
		LastFrameSent = Sent;
		bHasFrame = true;
	}

	bool IsReady() const { return bHasTransit && FrameInterval > 0.0; }

	// Seconds a frame waits before it is shown, beyond the fastest transit.
	double Depth(double JitterMs) const { return FrameInterval + JitterMargin * JitterMs / 1000.0; }

	// The sender time to show at receiver time Now.
	double SenderTimeAt(double Now, double JitterMs) const
	{
		return Now - std::min(PreviousMin, CurrentMin) - Depth(JitterMs);
	}

	double GetFrameInterval() const { return FrameInterval; }

private:
	bool bHasTransit = false;
	double WindowStart = 0.0;
	double CurrentMin = 0.0;
	double PreviousMin = 0.0;
	bool bHasFrame = false;
	double LastFrameSent = 0.0;
	double FrameInterval = 0.0;  // smoothed spacing of frame send times
};

// The match time to show for a sender time, interpolated between the frames
// received so far (their send times and match times, both ascending).
inline double MatchTimeAt(const std::vector<double>& SentTimes, const std::vector<double>& MatchTimes, double SenderTime)
{
	if (SentTimes.empty() || SentTimes.size() != MatchTimes.size())
	{
		return MatchTimes.empty() ? 0.0 : MatchTimes.back();
	}
	const SamplePoint S = FindSample(SentTimes, SenderTime);
	return MatchTimes[S.Index0] + (MatchTimes[S.Index1] - MatchTimes[S.Index0]) * S.Alpha;
}

// What the receiver made of a datagram.
enum class Verdict
{
	Use,        // new, from the stream being followed: apply it
	NewStream,  // match info that starts a new stream: reset, then apply it
	Malformed,  // not a valid packet
	Ignored,    // not from the stream being followed
	Duplicate,  // a sequence number seen before (or too old to tell)
	Stale,      // a frame older than the newest one: too late to show
	Repeat,     // another copy of an event already received
};

// The receiver rules from docs/telemetry.md, the same as the Python
// listener's: follow one stream at a time, starting from its match info; let
// another stream take over only after a bye or StreamTimeout of silence (the
// sender restarted); drop duplicates, stale frames and repeated events.
class StreamReceiver
{
public:
	Verdict Receive(const std::uint8_t* Data, std::size_t Size, double ArrivalSeconds, Packet& Out)
	{
		std::string Error;
		if (!ParsePacket(Data, Size, Out, Error))
		{
			++Malformed;
			return Verdict::Malformed;
		}
		Verdict Result = Verdict::Use;
		if (!bFollowing || Out.Head.Stream != Stream)
		{
			const bool bFree = !bFollowing || bEnded || ArrivalSeconds - LastArrival > StreamTimeout;
			if (Out.Head.Type != PacketType::Hello || !bFree)
			{
				++Ignored;
				return Verdict::Ignored;
			}
			Follow(Out.Head.Stream);
			Result = Verdict::NewStream;
		}
		LastArrival = ArrivalSeconds;
		if (!Link.Add(Out.Head.Sequence, Out.Head.SentMs, ArrivalSeconds))
		{
			return Verdict::Duplicate;
		}
		Playout.AddPacket(Out.Head.SentMs, ArrivalSeconds);

		if (Out.Head.Type == PacketType::Frame)
		{
			if (bHasFrame && Out.FrameData.Time <= NewestFrameTime)
			{
				++StaleFrames;
				return Verdict::Stale;
			}
			bHasFrame = true;
			NewestFrameTime = Out.FrameData.Time;
			Playout.AddFrame(Out.Head.SentMs);
		}
		else if (Out.Head.Type == PacketType::Event && !Events.insert(Out.EventId).second)
		{
			return Verdict::Repeat;
		}
		else if (Out.Head.Type == PacketType::Bye)
		{
			bEnded = true;
		}
		return Result;
	}

	bool IsFollowing() const { return bFollowing; }
	bool HasEnded() const { return bEnded; }
	bool IsQuiet(double Now) const { return bFollowing && Now - LastArrival > StreamTimeout; }
	std::uint16_t GetStream() const { return Stream; }
	const LinkStats& GetLink() const { return Link; }
	const PlayoutBuffer& GetPlayout() const { return Playout; }

	std::uint64_t Malformed = 0;    // all streams
	std::uint64_t Ignored = 0;      // all streams
	std::uint64_t StaleFrames = 0;  // the stream being followed

private:
	void Follow(std::uint16_t NewStream)
	{
		bFollowing = true;
		Stream = NewStream;
		bEnded = false;
		Link = LinkStats();
		Playout = PlayoutBuffer();
		Events.clear();
		bHasFrame = false;
		NewestFrameTime = 0.0;
		StaleFrames = 0;
	}

	bool bFollowing = false;
	std::uint16_t Stream = 0;
	bool bEnded = false;
	double LastArrival = 0.0;
	LinkStats Link;
	PlayoutBuffer Playout;
	std::unordered_set<std::uint32_t> Events;
	bool bHasFrame = false;
	double NewestFrameTime = 0.0;
};

}  // namespace rlcore::telemetry
