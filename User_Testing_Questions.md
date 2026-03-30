# Hotel Reservations Dataset: SQL Query QA

*Note: For the first section, 90% accurate answers were provided by the Qwen 14b model.*

---

Part 1: Complex Queries

1. Multi-condition Counting
Question: How many canceled bookings arrived in the year 2018 that had a lead time strictly greater than 100 days and 0 special requests?

Answer:Missing API key for higher model. Continuing with standard client LLM.*
In the year 2018, there were 4,427 cancellations where guests had booked their stay more than 100 days in advance and did not make any special requests.

SELECT COUNT(*) FROM hotel_reservations WHERE booking_status = 'Canceled' AND arrival_year = 2018 AND lead_time > 100 AND no_of_special_requests = 0;

2. Conditional Averages
Question: What is the average price per room for "Offline" bookings in 2017 that had strictly greater than 0 previous cancellations? (Round to 2 decimal places)

Answer: Missing API key for higher model. Continuing with standard client LLM.
For 'Offline' bookings in 2017 that had more than zero previous cancellations, the average price per room was $87.76.

SQL
SELECT ROUND(AVG(avg_price_per_room), 2) AS avg_price_per_room FROM hotel_reservations WHERE arrival_year = 2017 AND market_segment_type = 'Offline' AND no_of_previous_cancellations > 0;
3. Grouping with Max
Question: Which room_type_reserved has the highest average lead time for non-canceled bookings?

Answer: Missing API key for higher model. Continuing with standard client LLM.
For non-canceled bookings, the room type with the highest average lead time is Room Type 2, which has an average lead time of 74.16 days. This means that on average, guests who book Room Type 2 do so about 74 days before their stay begins compared to other room types.

SQL
SELECT room_type_reserved, AVG(lead_time) AS avg_lead_time FROM hotel_reservations WHERE booking_status = 'Not_Canceled' GROUP BY room_type_reserved ORDER BY avg_lead_time DESC LIMIT 1;
4. Percentages & Filtering
Question: What percentage of all bookings with 'Meal Plan 1' required a car parking space? (Round to 2 decimal places)

Answer: Missing API key for higher model. Continuing with standard client LLM.
Based on the data, 3.34% of all bookings that selected 'Meal Plan 1' also required a car parking space.

SQL
SELECT ROUND( SUM(CASE WHEN required_car_parking_space > 0 THEN 1 ELSE 0 END) / COUNT(*) * 100, 2 ) AS percentage FROM hotel_reservations WHERE type_of_meal_plan = 'Meal Plan 1';
5. Repeated Guests & Cancellations
Question: What is the total sum of no_of_week_nights booked by repeated guests who have exactly 0 previous cancellations?

Answer: Missing API key for higher model. Continuing with standard client LLM.
Based on the information provided, repeated guests who have not canceled any previous bookings (0 cancellations) have booked a total of 699 week nights at the hotel.

SQL
SELECT SUM(no_of_week_nights) AS total_week_night_bookings FROM hotel_reservations WHERE repeated_guest = 1 AND no_of_previous_cancellations = 0;
6. Revenue Calculation
Question: In the year 2018, which arrival month generated the highest total approximate revenue for Not_Canceled bookings? (Calculate approximate revenue per booking as avg_price_per_room multiplied by the sum of no_of_weekend_nights and no_of_week_nights).

Answer: Missing API key for higher model. Continuing with standard client LLM.
In 2018, the month that generated the highest total approximate revenue for non-canceled bookings was October, with a total revenue of approximately $575,239. This figure is calculated based on the average price per room multiplied by the number of nights stayed, including both weekend and week nights.

SQL
SELECT arrival_month, SUM(avg_price_per_room * (no_of_weekend_nights + no_of_week_nights)) AS total_revenue FROM hotel_reservations WHERE booking_status = 'Not_Canceled' AND arrival_year = 2018 GROUP BY arrival_month ORDER BY total_revenue DESC LIMIT 1;

7. Comparative Aggregation
Question: What is the absolute difference in the average lead time between the "Online" and "Offline" market segments specifically for Canceled bookings? (Round to 2 decimal places)

Answer: Missing API key for higher model. Continuing with standard client LLM.
The absolute difference in average lead time between Online and Offline market segments for canceled bookings is 93.97 days. This means that there's a significant difference in how far in advance these two segments typically cancel their reservations.

SQL
SELECT ROUND( ABS( AVG(CASE WHEN market_segment_type = 'Online' THEN lead_time ELSE NULL END) - AVG(CASE WHEN market_segment_type = 'Offline' THEN lead_time ELSE NULL END) ), 2 ) AS avg_lead_time_difference FROM hotel_reservations WHERE booking_status = 'Canceled';
8. Hyper-specific Cohorts
Question: How many bookings involve exactly 2 adults, exactly 1 child, require a car parking space, and have at least 1 special request?

Answer: Missing API key for higher model. Continuing with standard client LLM.
There are 61 bookings that match your criteria: they involve exactly two adults and one child, require a car parking space, and include at least one special request.

SQL
SELECT COUNT(*) AS booking_count FROM hotel_reservations WHERE no_of_adults = 2 AND no_of_children = 1 AND required_car_parking_space > 0 AND no_of_special_requests >= 1;
9. Monthly Proportions
Question: Of all the bookings that arrived in December (arrival_month = 12), what exact proportion (percentage) of them were canceled?

Answer: According to the data, 13.31% of the bookings that arrived in December were canceled.

SQL
SELECT COUNT(CASE WHEN booking_status = 'Canceled' THEN Booking_ID ELSE NULL END) * 100.0 / COUNT(*) AS cancellation_percentage FROM hotel_reservations WHERE arrival_month = 12;
10. Conditional Maximums
Question: What is the highest lead time among bookings that were Canceled and had exactly 2 special requests?

Answer: Missing API key for higher model. Continuing with standard client LLM.
Among canceled bookings that had exactly two special requests, the longest lead time was 350 days. That means the booking was made more than a year in advance before it was eventually canceled.

SQL
SELECT MAX(lead_time) AS max_lead_time FROM hotel_reservations WHERE booking_status = 'Canceled' AND no_of_special_requests = 2;
11. Segmented Cancellation Rates
Question: Which market_segment_type has the lowest cancellation rate for bookings that involve children (where no_of_children > 0)?

Answer: Missing API key for higher model. Continuing with standard client LLM.
The market segment type with the lowest cancellation rate for bookings that involve children is "Complementary," and interestingly, it has a cancellation rate of 0%, meaning no cancellations were made in this category.

SQL
SELECT market_segment_type, SUM(CASE WHEN booking_status = 'Canceled' THEN 1 ELSE 0 END) / COUNT(*) * 100 AS cancellation_rate FROM hotel_reservations WHERE no_of_children > 0 GROUP BY market_segment_type ORDER BY cancellation_rate ASC LIMIT 1;
12. Combined Stay Length Values
Question: What is the average price per room for Not_Canceled bookings that stayed exactly 7 total nights (weekend nights + week nights)?

Answer: Missing API key for higher model. Continuing with standard client LLM.
For bookings that were not canceled and stayed exactly 7 nights, the average price per room is approximately $98.43.

SQL
SELECT AVG(avg_price_per_room) AS avg_price_per_room FROM hotel_reservations WHERE booking_status = 'Not_Canceled' AND no_of_weekend_nights + no_of_week_nights = 7;
13. Column Comparisons
Question: How many bookings have more weekend nights than week nights? (Exclude any bookings that have 0 weekend nights from this count).

Answer: Missing API key for higher model. Continuing with standard client LLM.
Out of all the bookings, there are 4,775 that have more weekend nights than weeknights, and none of these bookings have zero weekend nights included in this count.

SQL
SELECT COUNT(*) AS booking_count FROM hotel_reservations WHERE no_of_weekend_nights > no_of_week_nights AND no_of_weekend_nights > 0;
14. Multiple Categorical Matching
Question: How many bookings with 'Room_Type 1' and a 'Not Selected' meal plan were made by repeated guests?

Answer: There were 30 bookings made by repeated guests who chose Room Type 1 and selected the 'Not Selected' meal plan option.

SQL
SELECT COUNT(*) FROM hotel_reservations WHERE room_type_reserved = 'Room_Type 1' AND type_of_meal_plan = 'Not Selected' AND repeated_guest = 1;
15. Medians & Value Thresholds
Question: What is the median lead time for bookings with an avg_price_per_room strictly greater than $150?

Answer: (No answer provided)

16. Most Frequent Occurrences (Mode)
Question: What was the most common arrival_date (day of the month) for bookings made specifically in July 2018?

Answer: Missing API key for higher model. Continuing with standard client LLM.
The most common arrival date for bookings made in July 2018 was the 6th, with 133 bookings recorded on that day.

SQL
SELECT arrival_date AS most_common_arrival_date, COUNT(*) AS booking_count FROM hotel_reservations WHERE arrival_year = 2018 AND arrival_month = 7 GROUP BY arrival_date ORDER BY booking_count DESC LIMIT 1;
17. Logical Contradictions in Behavior
Question: How many guests successfully completed 5 or more previous bookings (no_of_previous_bookings_not_canceled >= 5), but Canceled their current booking?

Answer: Missing API key for higher model. Continuing with standard client LLM.
Based on the information provided, there are 2 guests who have successfully completed five or more previous bookings but canceled their current booking.

SQL
SELECT COUNT(*) FROM hotel_reservations WHERE no_of_previous_bookings_not_canceled >= 5 AND booking_status = 'Canceled';
18. Zero-dollar Analysis
Question: What is the average total stay length (weekend + week nights) for bookings that paid an avg_price_per_room of exactly $0.0?

Answer: Missing API key for higher model. Continuing with standard client LLM.
For bookings that paid an average price per room of exactly $0.0, the average total stay length is approximately 1.43 nights, which includes both weekend and week nights.

SQL
SELECT AVG(no_of_weekend_nights + no_of_week_nights) AS avg_total_stay_length FROM hotel_reservations WHERE avg_price_per_room = 0.0;
19. Multi-column Grouping
Question: Which arrival year and month combination saw the most bookings with 3 or more special requests? (Format: YYYY-MM)

Answer: Missing API key for higher model. Continuing with standard client LLM.
In October 2018, there were the most bookings with 3 or more special requests, totaling 133 such bookings that month.

SQL
SELECT arrival_year, arrival_month, COUNT(*) AS booking_count FROM hotel_reservations WHERE no_of_special_requests >= 3 GROUP BY arrival_year, arrival_month ORDER BY booking_count DESC LIMIT 1;
20. Top Percentile Distributions
Question: What is the overall cancellation rate of bookings that have a lead time landing within the top 10% of all lead times? (Using >= 90th percentile)

Answer: (No answer provided)

Part 2: Core Dataset Queries
Dataset Geometry & Column Names
1. What is the total number of rows (bookings) in the dataset?
Answer: 36275. The hotel_reservations dataset contains a total of 36,275 rows.

SQL
SELECT COUNT(*) AS total_rows FROM hotel_reservations;
2. How many columns are in the dataset?
Answer: 19

Database: hotel_db

Table: hotel_reservations

Columns:

Booking_ID (text, nullable)

no_of_adults (bigint, nullable)

no_of_children (bigint, nullable)

no_of_weekend_nights (bigint, nullable)

no_of_week_nights (bigint, nullable)

type_of_meal_plan (text, nullable)

required_car_parking_space (bigint, nullable)

room_type_reserved (text, nullable)

lead_time (bigint, nullable)

arrival_year (bigint, nullable)

arrival_month (bigint, nullable)

arrival_date (bigint, nullable)

market_segment_type (text, nullable)

repeated_guest (bigint, nullable)

no_of_previous_cancellations (bigint, nullable)

no_of_previous_bookings_not_canceled (bigint, nullable)

avg_price_per_room (double, nullable)

no_of_special_requests (bigint, nullable)

booking_status (text, nullable)

3. What is the exact name of the first column?
Answer: Booking_ID

4. What is the exact name of the last column?
Answer: booking_status

Adult & Child Counts
5. What is the maximum number of adults in a single booking?
Answer: 4

SQL
SELECT MAX(no_of_adults) AS max_adults FROM hotel_reservations;
6. What is the minimum number of adults in a single booking?
Answer: 0

SQL
SELECT MIN(no_of_adults) AS min_no_of_adults FROM hotel_reservations;
7. How many bookings have exactly 0 adults?
Answer: 139

SQL
SELECT COUNT(*) AS num_bookings_with_zero_adults FROM hotel_reservations WHERE no_of_adults = 0;
8. How many bookings have exactly 1 adult?
Answer: 7695

SQL
SELECT COUNT(*) AS number_of_bookings_with_one_adult FROM hotel_reservations WHERE no_of_adults = 1;
9. How many bookings have exactly 2 adults?
Answer: 26108

SQL
SELECT COUNT(*) AS number_of_bookings_with_two_adults FROM hotel_reservations WHERE no_of_adults = 2;
10. How many bookings have 3 or more adults?
Answer: 2333

SQL
SELECT COUNT(*) AS number_of_bookings_with_three_or_more_adults FROM hotel_reservations WHERE no_of_adults >= 3;
11. What is the maximum number of children in a single booking?
Answer: 10

SQL
SELECT MAX(no_of_children) AS max_children FROM hotel_reservations;
12. How many bookings have exactly 0 children?
Answer: 33577

SQL
SELECT COUNT(*) AS number_of_bookings_with_zero_children FROM hotel_reservations WHERE no_of_children = 0;
13. How many bookings have exactly 1 child?
Answer: 1618

SQL
SELECT COUNT(*) AS number_of_bookings_with_one_child FROM hotel_reservations WHERE no_of_children = 1;
14. How many bookings have exactly 2 children?
Answer: 1058

SQL
SELECT COUNT(*) AS number_of_bookings_with_two_children FROM hotel_reservations WHERE no_of_children = 2;
15. How many bookings have 3 or more children?
Answer: 22

SQL
SELECT COUNT(*) AS number_of_bookings FROM hotel_reservations WHERE no_of_children >= 3;
Stay Length (Weekend & Week Nights)
16. What is the maximum number of weekend nights booked?
Answer: 7

SQL
SELECT MAX(no_of_weekend_nights) AS max_weekend_nights_booked FROM hotel_reservations;
17. How many bookings include exactly 0 weekend nights?
Answer: 16872

SQL
SELECT COUNT(*) AS booking_count FROM hotel_reservations WHERE no_of_weekend_nights = 0;
18. How many bookings include exactly 1 weekend night?
Answer: 9995

SQL
SELECT COUNT(*) AS number_of_bookings_with_one_weekend_night FROM hotel_reservations WHERE no_of_weekend_nights = 1;
19. How many bookings include exactly 2 weekend nights?
Answer: 9071

SQL
SELECT COUNT(*) AS number_of_bookings FROM hotel_reservations WHERE no_of_weekend_nights = 2;
20. What is the overall average number of weekend nights booked (rounded to 2 decimal places)?
Answer: 0.81

SQL
SELECT ROUND(AVG(no_of_weekend_nights), 2) AS avg_weekend_nights FROM hotel_reservations;
21. What is the maximum number of week nights booked?
Answer: 17

SQL
SELECT MAX(no_of_week_nights) AS max_week_nights_booked FROM hotel_reservations;
22. How many bookings include exactly 0 week nights?
Answer: 2387

SQL
SELECT COUNT(*) AS booking_count FROM hotel_reservations WHERE no_of_week_nights = 0;
23. What is the overall average number of week nights booked (rounded to 2 decimal places)?
Answer: 2.20

SQL
SELECT ROUND(AVG(no_of_week_nights), 2) AS avg_week_nights FROM hotel_reservations;
24. How many bookings include exactly 5 week nights?
Answer: 1614

SQL
SELECT COUNT(*) AS booking_count FROM hotel_reservations WHERE no_of_week_nights = 5;
25. How many bookings include strictly more than 5 week nights?
Answer: 513

SQL
SELECT COUNT(*) AS num_bookings FROM hotel_reservations WHERE no_of_week_nights > 5;
Meal Plans & Amenities
26. How many unique types of meal plans are present?
Answer: 4

SQL
SELECT COUNT(DISTINCT type_of_meal_plan) AS unique_meal_plans FROM hotel_reservations;
27. What is the most frequently chosen meal plan type (mode)?
Answer: Meal Plan 1

SQL
SELECT type_of_meal_plan, COUNT(*) AS frequency FROM hotel_reservations GROUP BY type_of_meal_plan ORDER BY frequency DESC LIMIT 1;
28. How many bookings specifically selected 'Meal Plan 1'?
Answer: 27835

SQL
SELECT COUNT(*) FROM hotel_reservations WHERE type_of_meal_plan = 'Meal Plan 1';
29. How many bookings specifically selected 'Not Selected'?
Answer: 5130

SQL
SELECT COUNT(*) AS not_selected_count FROM hotel_reservations WHERE type_of_meal_plan = 'Not Selected';
30. How many bookings specifically selected 'Meal Plan 2'?
Answer: 3305

SQL
SELECT COUNT(*) AS meal_plan_2_bookings FROM hotel_reservations WHERE type_of_meal_plan = 'Meal Plan 2';
31. How many bookings specifically selected 'Meal Plan 3'?
Answer: 5

SQL
SELECT COUNT(*) FROM hotel_reservations WHERE type_of_meal_plan = 'Meal Plan 3';
32. How many bookings required a car parking space (value = 1)?
Answer: 1124

SQL
SELECT SUM(required_car_parking_space) AS total_bookings_with_parking_space FROM hotel_reservations;
33. How many bookings did NOT require a car parking space (value = 0)?
Answer: 35151

SQL
SELECT COUNT(*) AS no_of_bookings_without_parking_space FROM hotel_reservations WHERE required_car_parking_space = 0;
34. What exact percentage of bookings required a car parking space?
Answer: 3.10%

Room Types Reserved
35. How many unique room types are recorded in the dataset?
Answer: 7

SQL
SELECT COUNT(DISTINCT room_type_reserved) AS unique_room_types FROM hotel_reservations;
36. What is the most popular reserved room type (mode)?
Answer: Room_Type 1

SQL
SELECT room_type_reserved FROM hotel_reservations GROUP BY room_type_reserved ORDER BY COUNT(*) DESC LIMIT 1;
37. How many bookings are specifically for 'Room_Type 1'?
Answer: 28130

SQL
SELECT COUNT(*) AS booking_count FROM hotel_reservations WHERE room_type_reserved = 'Room_Type 1';
38. How many bookings are specifically for 'Room_Type 2'?
Answer: 692

SQL
SELECT COUNT(*) AS booking_count FROM hotel_reservations WHERE room_type_reserved = 'Room_Type 2';
39. How many bookings are specifically for 'Room_Type 3'?
Answer: 7

SQL
SELECT COUNT(*) AS booking_count FROM hotel_reservations WHERE room_type_reserved = 'Room_Type 3';
40. How many bookings are specifically for 'Room_Type 4'?
Answer: 6057

SQL
SELECT COUNT(*) AS booking_count FROM hotel_reservations WHERE room_type_reserved = 'Room_Type 4';
41. How many bookings are specifically for 'Room_Type 5'?
Answer: 265

SQL
SELECT COUNT(*) AS booking_count FROM hotel_reservations WHERE room_type_reserved = 'Room_Type 5';
42. How many bookings are specifically for 'Room_Type 6'?
Answer: 966

SQL
SELECT COUNT(*) AS booking_count FROM hotel_reservations WHERE room_type_reserved = 'Room_Type 6';
43. How many bookings are specifically for 'Room_Type 7'?
Answer: 158

SQL
SELECT COUNT(*) AS booking_count FROM hotel_reservations WHERE room_type_reserved = 'Room_Type 7';
Lead Time Patterns
44. What is the longest lead time in days (maximum)?
Answer: 443

SQL
SELECT MAX(lead_time) AS longest_lead_time FROM hotel_reservations;
45. What is the shortest lead time in days (minimum)?
Answer: 0

SQL
SELECT MIN(lead_time) AS shortest_lead_time FROM hotel_reservations;
46. What is the average lead time in days across all bookings (rounded to 2 decimal places)?
Answer: 85.23

SQL
SELECT ROUND(AVG(lead_time), 2) AS avg_lead_time FROM hotel_reservations;
47. What is the median lead time in days?
Answer: 57.0

48. How many bookings were made with a lead time of exactly 0 days (same-day)?
Answer: 1297

SQL
SELECT COUNT(*) AS same_day_bookings FROM hotel_reservations WHERE lead_time = 0;
49. How many bookings have a lead time strictly greater than 100 days?
Answer: 11890

SQL
SELECT COUNT(*) AS booking_count FROM hotel_reservations WHERE lead_time > 100;
50. How many bookings have a lead time strictly greater than 300 days?
Answer: 1124

SQL
SELECT COUNT(*) AS booking_count FROM hotel_reservations WHERE lead_time > 300;
Arrival Date Analysis (Year, Month, Date)
51. How many unique arrival years are recorded?
Answer: 2

SQL
SELECT COUNT(DISTINCT arrival_year) AS unique_arrival_years FROM hotel_reservations;
52. What is the earliest recorded arrival year?
Answer: 2017

SQL
SELECT MIN(arrival_year) AS earliest_arrival_year FROM hotel_reservations;
53. What is the latest recorded arrival year?
Answer: 2018

SQL
SELECT MAX(arrival_year) AS latest_arrival_year FROM hotel_reservations;
54. How many total bookings arrived in the year 2017?
Answer: 6514

SQL
SELECT COUNT(*) AS total_bookings_2017 FROM hotel_reservations WHERE arrival_year = 2017;
55. How many total bookings arrived in the year 2018?
Answer: 29761

SQL
SELECT COUNT(*) AS total_bookings_2018 FROM hotel_reservations WHERE arrival_year = 2018;
56. What is the most popular arrival month represented as a number (mode)?
Answer: 10 (October)

SQL
SELECT arrival_month AS mode_arrival_month FROM hotel_reservations GROUP BY arrival_month ORDER BY COUNT(*) DESC LIMIT 1;
57. What is the least popular arrival month represented as a number?
Answer: 1 (January)

SQL
SELECT arrival_month FROM hotel_reservations GROUP BY arrival_month ORDER BY COUNT(*) ASC LIMIT 1;
58. How many bookings arrived in January (month 1)?
Answer: 1014

SQL
SELECT COUNT(*) AS total_bookings_in_january FROM hotel_reservations WHERE arrival_month = 1;
59. How many bookings arrived in February (month 2)?
Answer: 1704

SQL
SELECT COUNT(*) AS number_of_bookings_in_february FROM hotel_reservations WHERE arrival_month = 2;
60. How many bookings arrived in March (month 3)?
Answer: 2358

SQL
SELECT COUNT(*) AS number_of_bookings FROM hotel_reservations WHERE arrival_month = 3;
61. How many bookings arrived in October (month 10)?
Answer: 5317

SQL
SELECT COUNT(*) AS number_of_bookings FROM hotel_reservations WHERE arrival_month = 10;
62. How many bookings arrived in November (month 11)?
Answer: 2980

SQL
SELECT COUNT(*) FROM hotel_reservations WHERE arrival_month = 11;
63. How many bookings arrived in December (month 12)?
Answer: 3021

SQL
SELECT COUNT(*) FROM hotel_reservations WHERE arrival_month = 12;
64. What is the most frequent arrival date (day of the month)?
Answer: 13

SQL
SELECT arrival_date FROM hotel_reservations GROUP BY arrival_date ORDER BY COUNT(*) DESC LIMIT 1;
65. What is the least frequent arrival date (day of the month)?
Answer: 31

SQL
SELECT arrival_date FROM hotel_reservations GROUP BY arrival_date ORDER BY COUNT(*) ASC LIMIT 1;
66. How many bookings have an arrival date of the 1st of the month?
Answer: 1133

SQL
SELECT COUNT(*) AS num_bookings FROM hotel_reservations WHERE arrival_date = 1;
67. How many bookings have an arrival date of the 15th of the month?
Answer: 1273

SQL
SELECT COUNT(*) FROM hotel_reservations WHERE arrival_date = 15;
68. How many bookings have an arrival date of the 31st of the month?
Answer: 578

SQL
SELECT COUNT(*) FROM hotel_reservations WHERE arrival_date = 31;
Market Segments
69. How many unique market segment types are present in the dataset?
Answer: 5

SQL
SELECT COUNT(DISTINCT market_segment_type) FROM hotel_reservations;
70. What is the single most common market segment type?
Answer: Online

SQL
SELECT market_segment_type FROM hotel_reservations GROUP BY market_segment_type ORDER BY COUNT(*) DESC LIMIT 1;
71. How many bookings belong to the 'Aviation' market segment?
Answer: 125

SQL
SELECT COUNT(*) FROM hotel_reservations WHERE market_segment_type = 'Aviation';
72. How many bookings belong to the 'Complementary' market segment?
Answer: 391

SQL
SELECT COUNT(*) FROM hotel_reservations WHERE market_segment_type = 'Complementary';
73. How many bookings belong to the 'Corporate' market segment?
Answer: 2017

SQL
SELECT COUNT(*) FROM hotel_reservations WHERE market_segment_type = 'Corporate';
74. How many bookings belong to the 'Offline' market segment?
Answer: 10528

SQL
SELECT COUNT(*) AS booking_count FROM hotel_reservations WHERE market_segment_type = 'Offline';
75. How many bookings belong to the 'Online' market segment?
Answer: 23214

SQL
SELECT COUNT(*) AS booking_count FROM hotel_reservations WHERE market_segment_type = 'Online';
Guest Loyalty (Repeats & Previous Histories)
76. How many total bookings were made by a repeated guest (value = 1)?
Answer: 930

SQL
SELECT COUNT(*) AS total_bookings_by_repeated_guests FROM hotel_reservations WHERE repeated_guest = 1;
77. How many total bookings were made by a non-repeated guest (value = 0)?
Answer: 35345

SQL
SELECT COUNT(*) AS total_bookings_by_non_repeated_guests FROM hotel_reservations WHERE repeated_guest = 0;
78. What percentage of bookings are from repeated guests (rounded to 2 decimal places)?
Answer: 2.56%

SQL
SELECT ROUND((SUM(CASE WHEN repeated_guest > 0 THEN 1 ELSE 0 END) / COUNT(*)) * 100, 2) AS percentage_of_repeated_guests FROM hotel_reservations;
79. What is the maximum number of previous cancellations recorded for a single guest?
Answer: 13

SQL
SELECT MAX(no_of_previous_cancellations) AS max_cancellations FROM hotel_reservations;
80. How many bookings record exactly 0 previous cancellations?
Answer: 35937

SQL
SELECT COUNT(*) AS no_of_bookings_with_0_cancellations FROM hotel_reservations WHERE no_of_previous_cancellations = 0;
81. How many bookings record exactly 1 previous cancellation?
Answer: 198

SQL
SELECT COUNT(*) AS booking_count FROM hotel_reservations WHERE no_of_previous_cancellations = 1;
82. How many bookings record 2 or more previous cancellations?
Answer: 140

SQL
SELECT COUNT(*) AS total_bookings_with_2_or_more_cancellations FROM hotel_reservations WHERE no_of_previous_cancellations >= 2;
83. What is the maximum number of previous non-canceled bookings for a single guest?
Answer: 58

SQL
SELECT MAX(no_of_previous_bookings_not_canceled) AS max_non_cancelled_bookings FROM hotel_reservations;
84. How many bookings record exactly 0 previous non-canceled bookings?
Answer: 35463

SQL
SELECT COUNT(*) AS booking_count FROM hotel_reservations WHERE no_of_previous_bookings_not_canceled = 0;
85. How many bookings record 1 or more previous non-canceled bookings?
Answer: 812

SQL
SELECT COUNT(*) AS number_of_bookings_with_previous_non_cancellations FROM hotel_reservations WHERE no_of_previous_bookings_not_canceled > 0;
Pricing Metrics
86. What is the highest average price per room in the dataset?
Answer: 540.0

SQL
SELECT MAX(avg_price_per_room) AS max_avg_price_per_room FROM hotel_reservations;
87. What is the lowest average price per room in the dataset?
Answer: 0.0

SQL
SELECT MIN(avg_price_per_room) AS lowest_avg_price FROM hotel_reservations;
88. What is the mean average price per room (rounded to 2 decimal places)?
Answer: 103.42

SQL
SELECT AVG(avg_price_per_room) AS mean_average_price_per_room FROM hotel_reservations;
(Question 89 missing from source)

90. How many bookings have an average price per room of exactly 0.0?
Answer: 545

SQL
SELECT COUNT(*) FROM hotel_reservations WHERE avg_price_per_room = 0;
91. How many bookings have an average price per room strictly greater than 150?
Answer: 3220

SQL
SELECT COUNT(*) AS booking_count FROM hotel_reservations WHERE avg_price_per_room > 150;
92. How many bookings have an average price per room strictly greater than 250?
Answer: 59

SQL
SELECT COUNT(*) AS booking_count FROM hotel_reservations WHERE avg_price_per_room > 250;
Special Requests
93. What is the maximum number of special requests made on a single booking?
Answer: 5

SQL
SELECT MAX(no_of_special_requests) AS max_special_requests FROM hotel_reservations;
94. How many bookings have exactly 0 special requests?
Answer: 19777

SQL
SELECT COUNT(*) AS num_bookings_with_zero_requests FROM hotel_reservations WHERE no_of_special_requests = 0;
95. How many bookings have exactly 1 special request?
Answer: 11373

SQL
SELECT COUNT(*) AS number_of_bookings_with_one_special_request FROM hotel_reservations WHERE no_of_special_requests = 1;
96. How many bookings have exactly 2 special requests?
Answer: 4364

SQL
SELECT COUNT(*) AS booking_count FROM hotel_reservations WHERE no_of_special_requests = 2;
97. How many bookings have exactly 3 special requests?
Answer: 675

SQL
SELECT COUNT(*) FROM hotel_reservations WHERE no_of_special_requests = 3;
Booking Outcomes
(Question 98 missing from source)

99. How many bookings ended up with the status 'Canceled'?
Answer: 11885

SQL
SELECT COUNT(*) AS canceled_bookings_count FROM hotel_reservations WHERE booking_status = 'Canceled';

100. How many bookings ended up with the status 'Not_Canceled'?
Answer: 24390

SQL
SELECT COUNT(*) FROM hotel_reservations WHERE booking_status = 'Not_Canceled';
