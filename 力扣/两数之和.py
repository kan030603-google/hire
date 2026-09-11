class Solution(object):
    def twoSum(self, nums, target):
        n=len(nums)
        map={}
        for i,num in enumerate(nums):
            if target-num in map:
                return (i,map[target-num])
            else:
                map[num]=i
        return None
        

if __name__=="__main__":
    n,target=map(int,input().split())
    nums=list(map(int,input().split()))
    s=Solution()
    print(" ".join(map(str,s.twoSum(nums,target))))