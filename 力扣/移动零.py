class Solution(object):
    def moveZeroes(self, nums):
        """
        :type nums: List[int]
        :rtype: None Do not return anything, modify nums in-place instead.
        """
        n=len(nums)
        i=0
        while i<n and nums[i] !=0 :
            i+=1
        p1,p2=i,i+1
        while p2<n:
            if nums[p2]!=0:
                nums[p2],nums[p1]=nums[p1],nums[p2]
                p1+=1
                p2+=1
            else:
                p2+=1
        return nums