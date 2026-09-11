class Solution:
    def threeSum(self, nums):
        nums.sort()
        n=len(nums)
        result=[]
        i=2
        while i<n:
            if i+1<n and nums[i+1]==nums[i]:
                i+=1
                continue
            left,right=0,i-1
            target=-nums[i]
            while left<right:
                if nums[left]+nums[right]==target:
                    result.append([nums[left],nums[right],nums[i]])
                    while left+1<n and nums[left+1]==nums[left]: left+=1
                    while right-1>0 and nums[right-1]==nums[right]:right-=1
                    left+=1
                    right-=1
                elif nums[left]+nums[right]<target:
                    left+=1
                else:
                    right-=1
            i+=1
        return result

if __name__=="__main__":
    n=int(input())
    nums=list(map(int,input().split()))
    s=Solution()

    print(*(" ".join(map(str,l)) for l in s.threeSum(nums)),sep="\n") 