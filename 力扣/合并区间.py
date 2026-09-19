class Solution():
    def merge(self, intervals):
        """
        :type intervals: List[List[int]]
        :rtype: List[List[int]]
        """
        intervals.sort()
        result=[intervals[0]]
        for interval in intervals:
            if interval[0]<=result[-1][1]:
                result[-1][1]=max(result[-1][1],interval[1])
            else:
                result.append(interval)
        return result