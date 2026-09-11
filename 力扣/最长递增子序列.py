class Solution():
    def lengthOfLIS(self,nums):
        n=len(nums)
        dp=[1]*n
        for i in range(n):
            for j in range(i):
                if nums[i]>nums[j]:
                    dp[i]=max(dp[i],dp[j]+1)
        return max(dp)

if __name__=="__main__":
    nums=list(map(int,input().split()))
    s=Solution()
    print(s.lengthOfLIS(nums))