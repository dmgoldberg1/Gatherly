#include "utils/time.h"
#include <chrono>
#include <iomanip>
#include <regex>
#include <sstream>
#include <stdexcept>

namespace gatherly {
int64_t TimestampMicros(const std::string& value) {
    // RFC3339 plus PostgreSQL's equivalent text representation on reads.
    static const std::regex pattern(R"(^(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2}):(\d{2})(?:\.(\d{1,6}))?(Z|([+-])(\d{2})(?::?(\d{2}))?)$)");
    std::smatch match;
    if (!std::regex_match(value, match, pattern)) throw std::invalid_argument("timestamp must include date, time and timezone");
    const auto number = [&](int i) { return std::stoi(match[i]); };
    using namespace std::chrono;
    year_month_day date{year{number(1)}, month{unsigned(number(2))}, day{unsigned(number(3))}};
    if (!date.ok() || number(1) < 1 || number(4)>23 || number(5)>59 || number(6)>59) throw std::invalid_argument("invalid timestamp");
    int offset = 0;
    if (match[8] != "Z") {
        const int hours = number(10), minutes = match[11].matched ? number(11) : 0;
        if (hours > 23 || minutes > 59) throw std::invalid_argument("invalid timezone");
        offset = (hours*60 + minutes) * (match[9] == "+" ? 1 : -1);
    }
    std::string fraction = match[7]; fraction.resize(6, '0');
    const auto time = sys_days(date) + hours(number(4)) + minutes(number(5)-offset) + seconds(number(6)) + microseconds(std::stoi(fraction));
    return duration_cast<microseconds>(time.time_since_epoch()).count();
}
std::string NormalizeTimestamp(const std::string& value) {
    if (value.empty()) return "";
    using namespace std::chrono;
    const sys_time<microseconds> time{microseconds{TimestampMicros(value)}};
    const auto date = year_month_day{floor<days>(time)};
    const hh_mm_ss clock{time - floor<days>(time)};
    std::ostringstream output;
    output << std::setfill('0') << std::setw(4) << int(date.year()) << '-' << std::setw(2) << unsigned(date.month()) << '-' << std::setw(2) << unsigned(date.day())
           << 'T' << std::setw(2) << clock.hours().count() << ':' << std::setw(2) << clock.minutes().count() << ':' << std::setw(2) << clock.seconds().count();
    if (clock.subseconds().count()) {
        std::ostringstream fraction; fraction << std::setfill('0') << std::setw(6) << clock.subseconds().count();
        auto text = fraction.str(); while (text.back() == '0') text.pop_back(); output << '.' << text;
    }
    return output.str() + 'Z';
}
}
