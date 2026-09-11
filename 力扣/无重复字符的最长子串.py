class Solution:
    def lengthOfLongestSubstring(self, s: str):
        strmap={}
        n=len(s)
        result=0
        left,right=0,0
        while right<n:
            if s[right] not in strmap:
                strmap[s[right]]=right
                right+=1
            else:
                result=max(right-left,result)
                left=max(strmap[s[right]]+1,left)
                strmap[s[right]]=right
                right+=1
        result=max(result,right-left)
        return result

if __name__=="__main__":
    s=input()
    solut=Solution()
    print(solut.lengthOfLongestSubstring(s))