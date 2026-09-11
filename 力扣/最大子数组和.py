class Solution():
    def maxSubArray(self, nums):
        n=len(nums)
        dp=[nums[0]]*n
        for i in range(1,n):
            if nums[i]+dp[i-1]>nums[i]:
                dp[i]=dp[i-1]+nums[i]
            else:
                dp[i]=nums[i]
        return max(dp)

if __name__=="__main__":
    n=int(input())
    nums=list(map(int,input().split()))
    s=Solution()
    print(s.maxSubArray(nums))